from __future__ import annotations

from datetime import datetime

from app.models import Instrument, Task, TaskExecutionSegment, TimeSlot
from app.services.instrument_status_service import mark_instrument_running
from app.services.instrument_occupancy_service import current_occupying_slot
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
) -> dict[str, str]:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise TaskExecutionNotFoundError("时间槽不存在")
    task = db.query(Task).filter(Task.id == slot.task_id).first()
    if not task:
        raise TaskExecutionNotFoundError("任务不存在")
    reconcile_task_status_from_slots(task, slot)
    started_at = datetime.now()
    slot = _resume_anchor_slot(task, slot, started_at)
    if advance_schedule:
        _advance_resumed_schedule(task, slot, started_at)
    _ensure_can_start(db, task, slot, allow_queue_insert)

    task.status = "running"
    if slot.plan_start and started_at > slot.plan_start:
        mark_task_delayed(task)
    if task.project:
        task.project.status = "active"
    for running_slot in _continuous_slots(db, slot):
        running_slot.status = "running"
        if running_slot.id == slot.id:
            running_slot.actual_start = started_at
            running_slot.actual_end = None
            mark_instrument_running(db, running_slot.instrument_id)
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
    occupying_slot = current_occupying_slot(
        db,
        slot.instrument_id,
        excluded_task_id=task.id,
    )
    if occupying_slot and occupying_slot.task:
        raise TaskExecutionInvalidError(
            f"仪器当前任务【{occupying_slot.task.name}】尚未结束，不能启动【{task.name}】"
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


def _advance_resumed_schedule(task: Task, anchor: TimeSlot, started_at: datetime) -> None:
    """Move the unstarted continuation slots to the actual resume time."""
    if anchor.actual_start is not None or not anchor.plan_start:
        return
    delta = started_at - anchor.plan_start
    if delta.total_seconds() == 0:
        return
    for slot in sorted(task.time_slots, key=lambda item: (item.plan_start, item.id)):
        if slot.lifecycle_status != "active" or slot.id < anchor.id or slot.actual_start is not None:
            continue
        if slot.status not in RUNNING_CONTINUATION_STATUSES or not slot.plan_start or not slot.plan_end:
            continue
        slot.plan_start += delta
        slot.plan_end += delta


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
