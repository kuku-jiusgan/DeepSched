from __future__ import annotations

from datetime import datetime

from app.models import Instrument, Task, TaskExecutionSegment, TimeSlot
from app.services.instrument_status_service import mark_instrument_running
from app.services.instrument_occupancy_service import current_occupying_task
from app.services.task_delay_status_service import mark_task_delayed
from app.domain.errors import DomainConflictError, DomainNotFoundError


COMPLETED_TASK_STATUSES = {"done", "completed"}
STARTABLE_SLOT_STATUSES = {"scheduled", "blocked", "paused", "interrupted"}
RUNNING_CONTINUATION_STATUSES = {"scheduled", "running", "blocked", "paused", "interrupted"}


class TaskExecutionNotFoundError(DomainNotFoundError):
    pass


class TaskExecutionInvalidError(DomainConflictError):
    pass


def start_task_execution(
    db,
    slot_id: int,
    operator_id: int | None = None,
    allow_queue_insert: bool = False,
    advance_schedule: bool = False,
    started_at: datetime | None = None,
) -> dict[str, str]:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise TaskExecutionNotFoundError("时间槽不存在")
    task = db.query(Task).filter(Task.id == slot.task_id).first()
    if not task:
        raise TaskExecutionNotFoundError("任务不存在")
    reconcile_task_status_from_slots(task, slot)
    # 暂停切换要把切换那一刻传进来。那条路径上目标时间槽被压成零长度锚点钉在切换
    # 时刻，而重排求解要跑几秒；这里若自己再取一次当前时间，锚点就永远落在它之前，
    # 恢复时会被判成"没有可恢复的未来活动时间槽"。
    started_at = started_at or datetime.now()
    slot = _resume_anchor_slot(task, slot, started_at)
    if advance_schedule:
        _advance_resumed_schedule(db, task, slot, started_at)
    _ensure_can_start(db, task, slot, allow_queue_insert)

    task.status = "running"
    if slot.plan_start and started_at > slot.plan_start:
        mark_task_delayed(task)
    if task.project:
        task.project.status = "active"
    # Only the slot actually started by the operator is running. Future
    # continuation slots remain scheduled so pause/switch replans can replace
    # them and preserve the task's remaining planned workload.
    slot.status = "running"
    slot.actual_start = started_at
    slot.actual_end = None
    mark_instrument_running(db, slot.instrument_id)
    _normalize_future_continuations(task, slot)
    ensure_running_state_consistent(task, slot)
    ensure_running_continuation_consistent(task, slot)
    db.add(TaskExecutionSegment(
        task_id=task.id,
        slot_id=slot.id,
        instrument_id=slot.instrument_id,
        operator_id=operator_id,
        started_at=started_at,
    ))
    return {"status": "ok"}


def ensure_running_state_consistent(task: Task, slot: TimeSlot) -> None:
    if task.status != "running" or slot.lifecycle_status != "active" or slot.status != "running" or slot.actual_start is None:
        raise TaskExecutionInvalidError("任务与时间槽运行状态同步失败")


def ensure_running_continuation_consistent(task: Task, start_slot: TimeSlot) -> None:
    stale_slots = [
        slot for slot in task.time_slots
        if slot.lifecycle_status == "active" and slot.plan_end >= start_slot.plan_start
        and slot.status in {"paused", "blocked", "interrupted"}
    ]
    if stale_slots:
        raise TaskExecutionInvalidError("任务恢复后仍存在未同步的后续时间槽")


def _normalize_future_continuations(task: Task, start_slot: TimeSlot) -> None:
    """Keep unstarted continuation slots schedulable after a resume."""
    for continuation in task.time_slots:
        if continuation.id == start_slot.id or continuation.lifecycle_status != "active":
            continue
        if continuation.actual_start is not None or continuation.plan_start < start_slot.plan_start:
            continue
        if continuation.status in {"paused", "blocked", "interrupted", "running"}:
            continuation.status = "scheduled"


def reconcile_task_status_from_slots(task: Task, requested_slot: TimeSlot) -> None:
    if task.status != "running":
        return
    has_running_slot = any(
        slot.lifecycle_status == "active"
        and (slot.status == "running" or (slot.actual_start is not None and slot.actual_end is None))
        for slot in task.time_slots
    )
    if not has_running_slot and requested_slot.status in STARTABLE_SLOT_STATUSES:
        task.status = requested_slot.status


def ensure_predecessors_completed(task: Task) -> None:
    incomplete = []
    for dependency in task.predecessors:
        for name in _incomplete_leaf_task_names(dependency.predecessor):
            if name not in incomplete:
                incomplete.append(name)
    if incomplete:
        names = "、".join(incomplete[:3])
        raise TaskExecutionInvalidError(f"前置任务【{names}】尚未完成，不能操作【{task.name}】")


def predecessors_completed(task: Task) -> bool:
    return all(
        not _incomplete_leaf_task_names(dependency.predecessor)
        for dependency in task.predecessors
    )


def _incomplete_leaf_task_names(task: Task) -> list[str]:
    if task.children:
        return [
            name
            for child in task.children
            for name in _incomplete_leaf_task_names(child)
        ]
    return [] if task.status in COMPLETED_TASK_STATUSES else [task.name]


def _ensure_can_start(db, task: Task, slot: TimeSlot, allow_queue_insert: bool = False) -> None:
    if task.status in COMPLETED_TASK_STATUSES or slot.status == "completed":
        raise TaskExecutionInvalidError("任务已经完成，不能重复开始")
    if task.status == "paused":
        ensure_paused_state_consistent(task)
    if task.status == "running" or any(
        task_slot.lifecycle_status == "active"
        and task_slot.actual_start is not None and task_slot.actual_end is None
        for task_slot in task.time_slots
    ):
        raise TaskExecutionInvalidError("任务已经开始，不能重复操作")
    if slot.status not in STARTABLE_SLOT_STATUSES:
        raise TaskExecutionInvalidError("当前任务状态不能开始")
    ensure_predecessors_completed(task)
    if not task.requires_instrument:
        return
    if not slot.instrument_id:
        raise TaskExecutionInvalidError("仪器任务尚未分配仪器，不能启动")
    instrument = db.query(Instrument).filter(Instrument.id == slot.instrument_id).first()
    if instrument and instrument.status == "fault":
        raise TaskExecutionInvalidError(
            f"仪器【{instrument.code} {instrument.name}】当前处于故障状态，不能启动任务"
        )
    if not allow_queue_insert:
        _ensure_earlier_instrument_tasks_completed(db, task, slot)
    occupying_task = current_occupying_task(
        db,
        slot.instrument_id,
        excluded_task_id=task.id,
    )
    if occupying_task:
        raise TaskExecutionInvalidError(
            f"仪器当前任务【{occupying_task.name}】尚未结束，不能启动【{task.name}】"
        )


def _ensure_earlier_instrument_tasks_completed(db, task: Task, slot: TimeSlot) -> None:
    earlier_slot = (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            TimeSlot.instrument_id == slot.instrument_id,
            TimeSlot.task_id != task.id,
            TimeSlot.plan_start < slot.plan_start,
            TimeSlot.plan_end > slot.plan_start,
            TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
            TimeSlot.lifecycle_status == "active",
            Task.status.notin_(["done", "completed"]),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .first()
    )
    if earlier_slot and earlier_slot.task:
        project = earlier_slot.task.project
        project_label = " · ".join(
            value for value in [project.code, project.name] if value
        ) if project else "未归属项目"
        raise TaskExecutionInvalidError(
            f"仪器前序项目【{project_label}】任务【{earlier_slot.task.name}】"
            f"尚未完成，不能启动【{task.name}】"
        )


def _continuous_slots(db, start_slot: TimeSlot) -> list[TimeSlot]:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == start_slot.task_id,
            TimeSlot.plan_end >= start_slot.plan_start,
            TimeSlot.status.in_(RUNNING_CONTINUATION_STATUSES),
            TimeSlot.lifecycle_status == "active",
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )


def _resume_anchor_slot(task: Task, requested_slot: TimeSlot, started_at: datetime) -> TimeSlot:
    if task.status not in {"paused", "blocked", "interrupted"}:
        return requested_slot
    candidates = sorted(
        (
            slot for slot in task.time_slots
            if slot.lifecycle_status == "active"
            and slot.status in STARTABLE_SLOT_STATUSES and slot.plan_end >= started_at
        ),
        key=lambda slot: (slot.plan_start, slot.id),
    )
    if candidates:
        return candidates[0]
    raise TaskExecutionInvalidError("任务没有可恢复的未来活动时间槽，请先重新排程")


def _advance_resumed_schedule(db, task: Task, anchor: TimeSlot, started_at: datetime) -> None:
    """把还没开始的后续时间槽按工作日历重新铺放到实际恢复时刻之后。

    此前是给每个时间槽加一个墙钟差值，完全不看工作日历。周六恢复、或临近 20:00
    收工时恢复，会把计划直接推到周末和工作时段之外（实测出现过 21:01 结束、
    周日 09:31 开始的时间槽），而且每次恢复都再累加一次差值，排程会被一步步
    推离工作日。

    改为按有效工时重新铺放：跨越非工作时间时拆成多个时间槽，与求解器产出的形态
    一致。恢复动作发生在非工作时间时，计划自然落到下一个工作时段——那段时间的
    实际执行仍然完整记在执行流水里，只是不计入进度。
    """
    from app.services.schedule_working_time_service import working_time_chunks

    if anchor.actual_start is not None or not anchor.plan_start:
        return
    slots = [
        slot for slot in sorted(task.time_slots, key=lambda item: (item.plan_start, item.id))
        if slot.lifecycle_status == "active"
        and slot.id >= anchor.id
        and slot.actual_start is None
        and slot.status in RUNNING_CONTINUATION_STATUSES
        and slot.plan_start and slot.plan_end
    ]
    if not slots:
        return
    total_hours = sum(
        (slot.plan_end - slot.plan_start).total_seconds() for slot in slots
    ) / 3600
    chunks = working_time_chunks(db, started_at, total_hours, anchor.instrument_id)
    if not chunks:
        return
    _apply_replanned_chunks(db, anchor, slots, chunks)


def _apply_replanned_chunks(db, anchor: TimeSlot, slots: list[TimeSlot], chunks: list) -> None:
    """把重新铺好的时间段落回时间槽：不够就新建，多余的作废。"""
    for slot, (chunk_start, chunk_end) in zip(slots, chunks):
        slot.plan_start = chunk_start
        slot.plan_end = chunk_end
    for slot in slots[len(chunks):]:
        slot.lifecycle_status = "superseded"
        slot.superseded_reason = "恢复后按工作日历重排"
        slot.status = "cancelled"
    for chunk_start, chunk_end in chunks[len(slots):]:
        db.add(TimeSlot(
            task_id=anchor.task_id,
            instrument_id=anchor.instrument_id,
            schedule_run_id=anchor.schedule_run_id,
            plan_start=chunk_start,
            plan_end=chunk_end,
            tier=anchor.tier,
            status="scheduled",
            lifecycle_status="active",
            is_night_run=False,
        ))


def ensure_paused_state_consistent(task: Task) -> None:
    has_open_slot = any(
        task_slot.lifecycle_status == "active"
        and task_slot.actual_start is not None and task_slot.actual_end is None
        for task_slot in task.time_slots
    )
    active_slot_ids = {slot.id for slot in task.time_slots if slot.lifecycle_status == "active"}
    has_open_segment = any(
        segment.ended_at is None and segment.slot_id in active_slot_ids
        for segment in task.execution_segments
    )
    if has_open_slot or has_open_segment:
        raise TaskExecutionInvalidError("任务暂停状态不完整，请先修复执行记录")
