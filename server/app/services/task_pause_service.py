from __future__ import annotations

from datetime import datetime

from app.domain.errors import DomainConflictError, DomainNotFoundError, DomainValidationError
from app.models import Task, TaskExecutionSegment, TimeSlot
from app.services.audit_log_service import record_audit_log
from app.services.instrument_status_service import refresh_instrument_status
from app.services.task_execution_service import predecessors_completed, start_task_execution
from app.services.schedule_working_time_service import working_hours_between
from app.services.task_progress_service import planned_task_minutes
from app.services.task_pause_solver_service import replan_pause_switch
from app.services.task_pause_window_service import (
    CANDIDATE_SLOT_STATUSES,
)


CANDIDATE_TASK_STATUSES = {"pending", "scheduled", "paused", "blocked", "interrupted"}


def list_switch_candidates(db, source_slot_id: int) -> list[dict]:
    source_slot, source_task = _running_source(db, source_slot_id)
    if not source_slot.instrument_id:
        return []

    slots = (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            TimeSlot.instrument_id == source_slot.instrument_id,
            TimeSlot.task_id != source_task.id,
            TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
            TimeSlot.lifecycle_status == "active",
            TimeSlot.actual_start.is_(None),
            Task.status.in_(CANDIDATE_TASK_STATUSES),
            Task.requires_instrument.is_(True),
            ~Task.children.any(),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    candidates = []
    seen_task_ids: set[int] = set()
    for slot in slots:
        task = slot.task
        if task.id in seen_task_ids or not predecessors_completed(task):
            continue
        if task.requires_human and task.assignee_id is None:
            continue
        seen_task_ids.add(task.id)
        candidates.append(_candidate_out(slot, task, _cancelled_night_runs(db, source_slot, slot)))
    return candidates


def pause_and_switch_task(
    db,
    source_slot_id: int,
    reason: str,
    operator,
    target_slot_id: int | None = None,
) -> dict[str, str]:
    clean_reason = reason.strip()
    if not clean_reason:
        raise DomainValidationError("请填写暂停原因")
    source_slot, source_task = _running_source(db, source_slot_id)
    target_slot = _validated_target(db, source_slot, target_slot_id) if target_slot_id else None

    paused_at = datetime.now()
    paused_slots = _running_task_slots(db, source_slot)
    for slot in paused_slots:
        slot.status = "paused"
    # 已经按计划边界结束的时间槽不再改写：任务被按天切割产生的中间边界用的是
    # 计划时间，暂停发生在它之后，不该回头覆盖它。只有还在跑、尚未结束的时间槽
    # 才写入真实的暂停时刻。
    source_slot.actual_end = source_slot.actual_end or paused_at
    source_task.executed_minutes = min(
        planned_task_minutes(source_task),
        int(source_task.executed_minutes or 0)
        + _elapsed_execution_minutes(
            db, source_task, paused_at,
            fallback_started_at=source_slot.actual_start,
            fallback_instrument_id=source_slot.instrument_id,
        ),
    )
    source_task.status = "paused"
    _close_execution_segment(db, source_slot, paused_at, clean_reason, operator.id)
    db.flush()
    for instrument_id in {slot.instrument_id for slot in paused_slots if slot.instrument_id}:
        refresh_instrument_status(db, instrument_id)

    target_task_name = None
    if target_slot:
        target_task_name = target_slot.task.name
        switch_time = _insert_target_into_source_schedule(db, source_slot, target_slot, paused_at)
        db.flush()
        # 恢复接替任务必须用切换那一刻，不能让下游再取一次当前时间：中间隔着一次
        # 重排求解（上限 8 秒），锚点时间槽被压成 plan_start == plan_end == 切换
        # 时刻，晚几十秒回头看它就已经"过期"，接替一个已暂停的任务会被判成没有
        # 可恢复的未来时间槽。
        start_task_execution(
            db, target_slot.id, operator.id, allow_queue_insert=True, started_at=switch_time,
        )
        _promote_switched_instrument_slot(db, target_slot.task_id, paused_at)
        _discard_zero_length_anchor(db, target_slot.task_id)

    record_audit_log(
        db,
        operator.display_name or operator.username,
        "task_paused",
        "task",
        source_task.id,
        {
            "reason": clean_reason,
            "instrument_id": source_slot.instrument_id,
            "source_task_id": source_task.id,
            "source_slot_id": source_slot.id,
            "target_task_id": target_slot.task_id if target_slot else None,
            "target_slot_id": target_slot.id if target_slot else None,
        },
    )
    message = "任务已暂停，仪器已释放"
    if target_task_name:
        message = f"任务已暂停，已切换至【{target_task_name}】"
    return {"status": "ok", "message": message}


def _insert_target_into_source_schedule(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    started_at: datetime,
) -> datetime:
    return replan_pause_switch(db, source_slot, target_slot, started_at)


def _promote_switched_instrument_slot(db, task_id: int, started_at: datetime) -> None:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task or not task.requires_instrument:
        return
    active_slots = (
        db.query(TimeSlot)
        .filter(TimeSlot.task_id == task_id, TimeSlot.lifecycle_status == "active")
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    candidate = next(
        (slot for slot in active_slots if slot.plan_end and slot.plan_end > started_at and slot.status == "scheduled"),
        None,
    )
    if candidate is None:
        return
    candidate.status = "running"
    candidate.actual_start = started_at
    candidate.actual_end = None


def _discard_zero_length_anchor(db, task_id: int) -> None:
    """清掉切换时留下的零长度锚点时间槽。

    重排前会把接替任务的原时间槽压成 plan_start == plan_end 当作锚点，求解器
    另外生成真正的时间槽。锚点完成使命后若留在库里，同一个任务就会有两个 active
    的 running 槽：甘特图上多一个退化的块，_actual_running_slot 也可能挑中它，
    暂停时就会作用在一个零长度的槽上。只有确实存在其他活跃时间槽时才丢弃它。
    """
    active_slots = (
        db.query(TimeSlot)
        .filter(TimeSlot.task_id == task_id, TimeSlot.lifecycle_status == "active")
        .all()
    )
    anchors = [slot for slot in active_slots if slot.plan_start == slot.plan_end]
    if not anchors or len(anchors) == len(active_slots):
        return
    for anchor in anchors:
        anchor.lifecycle_status = "superseded"
        anchor.superseded_reason = "暂停切换锚点"
        anchor.status = "cancelled"


def _approval_ready_time(db, task: Task) -> datetime | None:
    from app.services.approval_gate_service import unapproved_gate_context

    bounds, _ = unapproved_gate_context(db, [task])
    return bounds.get(task.id)


def _running_source(db, slot_id: int) -> tuple[TimeSlot, Task]:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise DomainNotFoundError("时间槽不存在")
    task = slot.task
    if not task:
        raise DomainNotFoundError("任务不存在")
    if task.status != "running":
        raise DomainConflictError("只有正在运行且占用仪器的任务可以暂停")
    active_slot = _actual_running_slot(task, slot.instrument_id)
    if active_slot is not None:
        return active_slot, task
    # 跨时间段的空档期：任务被按天切成多个时间槽，上一段已按计划边界结束，
    # 下一段还没到（隔夜、周末、或被别的项目插队），此时没有"正在运行"的
    # 时间槽，但人确实还在这个任务上。执行流水才是人在不在干活的真实记录，
    # 时间槽只是计划，所以这里以流水为准，否则空档期里暂停会被拒绝，而同样
    # 状态下"完成"却是允许的，两条路对同一个状态给出不同结论。
    segment = _open_execution_segment(task)
    segment_slot = _segment_slot(db, segment) if segment else None
    # 还要求这个时间槽真实开始过。任务挂着 running 却没有任何时间槽被真正开始
    # 过，是脏状态而不是空档期，仍然要拒绝。
    if segment_slot is None or segment_slot.actual_start is None:
        raise DomainConflictError("任务与时间槽状态不一致，请刷新工作台后重试")
    return segment_slot, task


def _open_execution_segment(task: Task) -> TaskExecutionSegment | None:
    return next(
        (item for item in reversed(task.execution_segments) if item.ended_at is None),
        None,
    )


def _segment_slot(db, segment: TaskExecutionSegment) -> TimeSlot | None:
    if segment.slot_id is None:
        return None
    return db.query(TimeSlot).filter(TimeSlot.id == segment.slot_id).first()


def _actual_running_slot(task: Task, instrument_id: int | None) -> TimeSlot | None:
    return next(
        (
            item for item in task.time_slots
            if item.instrument_id == instrument_id
            and item.status == "running"
            and item.lifecycle_status == "active"
            and item.actual_start is not None
            and item.actual_end is None
        ),
        None,
    )


def _validated_target(db, source_slot: TimeSlot, target_slot_id: int) -> TimeSlot:
    candidates = list_switch_candidates(db, source_slot.id)
    candidate_ids = {candidate["slot_id"] for candidate in candidates}
    if target_slot_id not in candidate_ids:
        raise DomainConflictError("接替任务不满足仪器、状态或前置任务条件")
    return db.query(TimeSlot).filter(TimeSlot.id == target_slot_id).first()


def _running_task_slots(db, source_slot: TimeSlot) -> list[TimeSlot]:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == source_slot.task_id,
            TimeSlot.status == "running",
            TimeSlot.lifecycle_status == "active",
            TimeSlot.plan_end >= source_slot.plan_start,
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )


def _close_execution_segment(
    db,
    slot: TimeSlot,
    ended_at: datetime,
    reason: str,
    operator_id: int,
) -> None:
    segment = (
        db.query(TaskExecutionSegment)
        .filter(
            TaskExecutionSegment.task_id == slot.task_id,
            TaskExecutionSegment.ended_at.is_(None),
        )
        .order_by(TaskExecutionSegment.started_at.desc(), TaskExecutionSegment.id.desc())
        .first()
    )
    if segment:
        segment.ended_at = ended_at
        segment.end_reason = "paused"
        segment.pause_reason = reason
        return
    db.add(TaskExecutionSegment(
        task_id=slot.task_id,
        slot_id=slot.id,
        instrument_id=slot.instrument_id,
        operator_id=operator_id,
        started_at=slot.actual_start,
        ended_at=ended_at,
        end_reason="paused",
        pause_reason=reason,
    ))


def _elapsed_execution_minutes(
    db, task: Task, ended_at: datetime,
    fallback_started_at: datetime | None = None,
    fallback_instrument_id: int | None = None,
) -> int:
    """本次执行累计的有效工时（分钟）。

    executed_minutes 会被 planned_task_minutes 减去，用来决定求解器重排时
    还要排多久，也用来判断任务是否可以标记完成。两者都是工作量口径，所以
    这里必须按工作日历统计：按墙钟差值算的话，周五 18:00 开始、周一 10:00
    暂停会被记成 64 小时，任务看起来已经做完，重排时却只排 30 分钟。
    """
    segment = next(
        (item for item in reversed(task.execution_segments) if item.ended_at is None),
        None,
    )
    if not segment:
        if not fallback_started_at:
            return 0
        started_at = fallback_started_at
        instrument_id = fallback_instrument_id
    else:
        started_at = segment.started_at
        instrument_id = segment.instrument_id
    hours = working_hours_between(
        db, started_at, ended_at, instrument_id,
    )
    return max(0, int(hours * 60))


def _cancelled_night_runs(db, source_slot: TimeSlot, target_slot: TimeSlot) -> list[dict]:
    """切到这个目标会被一并作废的夜间运行。

    暂停切换会把尚未发生的时间槽全部推翻重排，其中的夜跑槽被作废后不会自动
    恢复——求解器不产出夜跑标记。取消本身是合理的（仪器已经让给别的项目，
    今晚这台机器跑不了原来的活），但不能悄无声息，得在确认前摆出来。
    """
    from app.services.task_pause_switch_context_service import build_pause_switch_context

    try:
        context = build_pause_switch_context(db, source_slot, target_slot, datetime.now())
    except Exception:  # 预览用途，算不出来就不提示，绝不能挡住候选列表
        return []
    runs = []
    for slot in context.replaceable_slots:
        if not slot.is_night_run or slot.task is None:
            continue
        project = slot.task.project
        runs.append({
            "project_code": project.code if project else "-",
            "task_name": slot.task.name,
            "assignee_name": slot.task.assignee_name,
            "plan_start": slot.plan_start,
            "plan_end": slot.plan_end,
        })
    return runs


def _candidate_out(slot: TimeSlot, task: Task, cancelled_night_runs: list[dict]) -> dict:
    project = task.project
    return {
        "slot_id": slot.id,
        "task_id": task.id,
        "task_name": task.name,
        "project_code": project.code if project else "-",
        "project_name": project.name if project else "-",
        "assignee_name": task.assignee.display_name if task.assignee else None,
        "plan_start": slot.plan_start,
        "plan_end": slot.plan_end,
        "is_paused": task.status == "paused",
        "cancelled_night_runs": cancelled_night_runs,
    }
