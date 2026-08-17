from __future__ import annotations

from datetime import datetime

from app.domain.errors import DomainConflictError, DomainNotFoundError, DomainValidationError
from app.models import Task, TaskExecutionSegment, TimeSlot
from app.services.audit_log_service import record_audit_log
from app.services.instrument_status_service import refresh_instrument_status
from app.services.schedule_delay_service import _load_working_options
from app.services.schedule_forward_slot_service import build_forward_slots
from app.services.task_execution_service import ensure_running_state_consistent, predecessors_completed, start_task_execution


CANDIDATE_TASK_STATUSES = {"pending", "scheduled", "paused", "blocked", "interrupted"}
CANDIDATE_SLOT_STATUSES = {"scheduled", "paused", "interrupted", "blocked"}


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
        seen_task_ids.add(task.id)
        candidates.append(_candidate_out(slot, task))
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
    source_slot.actual_end = paused_at
    source_task.status = "paused"
    _close_execution_segment(db, source_slot, paused_at, clean_reason, operator.id)
    db.flush()
    for instrument_id in {slot.instrument_id for slot in paused_slots if slot.instrument_id}:
        refresh_instrument_status(db, instrument_id)

    target_task_name = None
    if target_slot:
        target_task_name = target_slot.task.name
        _insert_target_into_source_schedule(db, source_slot, target_slot, paused_at)
        db.flush()
        start_task_execution(db, target_slot.id, operator.id, allow_queue_insert=True)

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
) -> None:
    switch_time = started_at.replace(second=0, microsecond=0)
    target_slots = _task_queue_slots(db, target_slot)
    source_slots = _task_queue_slots(db, source_slot)
    target_minutes = _slot_minutes(target_slots)
    source_minutes = _slot_minutes(source_slots)
    target_original_end = max(slot.plan_end for slot in target_slots)
    queue_reorder_end = _instrument_queue_end(db, source_slot.instrument_id, switch_time)
    intermediate_groups = _intermediate_task_slots(
        db, source_slot, target_slot, switch_time, queue_reorder_end or target_original_end,
    )

    historical_source_start = source_slot.actual_start or switch_time
    source_slot.plan_start = min(historical_source_start, switch_time)
    source_slot.plan_end = switch_time
    target_slot.plan_start = switch_time
    target_slot.plan_end = switch_time

    replaceable_slots = [slot for slot in source_slots if slot.id != source_slot.id]
    replaceable_slots.extend(slot for slot in target_slots if slot.id != target_slot.id)
    replaceable_slots.extend(slot for slots in intermediate_groups for slot in slots)
    for slot in replaceable_slots:
        db.delete(slot)
    db.flush()

    queue = [
        (target_slot.task, target_slot, target_minutes, target_slot.status, target_slot),
        (source_slot.task, None, source_minutes, "paused", source_slot),
    ]
    queue.extend(
        (slots[0].task, None, _slot_minutes(slots), slots[0].status, slots[0])
        for slots in intermediate_groups
    )
    cursor = switch_time
    options = _load_working_options(db, switch_time)
    for task, reusable_slot, duration_minutes, status, template_slot in queue:
        if duration_minutes <= 0:
            continue
        task_options = _task_schedule_options(options, task)
        ranges = build_forward_slots(
            db,
            task,
            template_slot.instrument_id,
            duration_minutes,
            cursor,
            task_options,
        )
        if not ranges:
            raise DomainConflictError(f"切换后无法为任务【{task.name}】找到可用工作时段")
        _save_reordered_ranges(
            db,
            task,
            reusable_slot,
            ranges,
            template_slot.instrument_id,
            status,
            template_slot,
        )
        db.flush()
        cursor = ranges[-1][1]


def _instrument_queue_end(db, instrument_id: int | None, switch_time: datetime) -> datetime | None:
    if instrument_id is None:
        return None
    return max(
        (
            slot.plan_end
            for slot in db.query(TimeSlot).filter(
                TimeSlot.instrument_id == instrument_id,
                TimeSlot.plan_start >= switch_time,
                TimeSlot.actual_start.is_(None),
                TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
            ).all()
        ),
        default=None,
    )


def _task_schedule_options(options: dict, task: Task) -> dict:
    project_end = task.project.end_date if task.project else None
    if not project_end or project_end >= options["horizon_end"]:
        return options
    return {**options, "horizon_end": project_end}


def _task_queue_slots(db, anchor: TimeSlot) -> list[TimeSlot]:
    slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == anchor.task_id,
            TimeSlot.instrument_id == anchor.instrument_id,
            TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
            TimeSlot.actual_end.is_(None),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    if all(slot.id != anchor.id for slot in slots):
        slots.append(anchor)
    return sorted(slots, key=lambda slot: (slot.plan_start, slot.id))


def _intermediate_task_slots(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    switch_time: datetime,
    target_original_end: datetime,
) -> list[list[TimeSlot]]:
    instrument_slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.instrument_id == source_slot.instrument_id,
            TimeSlot.task_id.notin_([source_slot.task_id, target_slot.task_id]),
            TimeSlot.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_start >= switch_time,
            TimeSlot.plan_start < target_original_end,
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    human_slots = []
    if source_slot.task.requires_human and source_slot.task.assignee_id is not None:
        human_slots = (
            db.query(TimeSlot)
            .join(Task, Task.id == TimeSlot.task_id)
            .filter(
                Task.requires_human.is_(True),
                Task.assignee_id == source_slot.task.assignee_id,
                TimeSlot.task_id.notin_([source_slot.task_id, target_slot.task_id]),
                TimeSlot.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
                TimeSlot.actual_start.is_(None),
                TimeSlot.plan_start >= switch_time,
                TimeSlot.plan_start < target_original_end,
            )
            .order_by(TimeSlot.plan_start, TimeSlot.id)
            .all()
        )
    slots = sorted(
        {slot.id: slot for slot in [*instrument_slots, *human_slots]}.values(),
        key=lambda slot: (slot.plan_start, slot.id),
    )
    grouped: dict[int, list[TimeSlot]] = {}
    for slot in slots:
        grouped.setdefault(slot.task_id, []).append(slot)
    if not grouped:
        return []
    all_slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id.in_(grouped),
            TimeSlot.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_start >= switch_time,
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    complete_groups: dict[int, list[TimeSlot]] = {task_id: [] for task_id in grouped}
    for slot in all_slots:
        complete_groups[slot.task_id].append(slot)
    return [complete_groups[task_id] for task_id in grouped]


def _slot_minutes(slots: list[TimeSlot]) -> int:
    return sum(
        max(0, int((slot.plan_end - slot.plan_start).total_seconds() / 60))
        for slot in slots
    )


def _save_reordered_ranges(
    db,
    task: Task,
    reusable_slot: TimeSlot | None,
    ranges: list[tuple[datetime, datetime]],
    instrument_id: int | None,
    status: str,
    template: TimeSlot,
) -> None:
    for index, (start, end) in enumerate(ranges):
        slot = reusable_slot if index == 0 and reusable_slot is not None else TimeSlot(
            task_id=task.id,
            schedule_run_id=template.schedule_run_id,
            instrument_id=instrument_id,
            tier=template.tier,
        )
        slot.plan_start = start
        slot.plan_end = end
        slot.status = status
        if slot is not reusable_slot:
            db.add(slot)


def _running_source(db, slot_id: int) -> tuple[TimeSlot, Task]:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise DomainNotFoundError("时间槽不存在")
    task = slot.task
    if not task:
        raise DomainNotFoundError("任务不存在")
    if task.status == "running":
        active_slot = _actual_running_slot(task, slot.instrument_id)
        if active_slot is not None:
            slot = active_slot
        try:
            ensure_running_state_consistent(task, slot)
        except DomainConflictError:
            raise DomainConflictError("任务与时间槽状态不一致，请刷新工作台后重试")
    if task.status != "running" or slot.actual_end is not None:
        raise DomainConflictError("只有正在运行且占用仪器的任务可以暂停")
    return slot, task


def _actual_running_slot(task: Task, instrument_id: int | None) -> TimeSlot | None:
    return next(
        (
            item for item in task.time_slots
            if item.instrument_id == instrument_id
            and item.status == "running"
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


def _candidate_out(slot: TimeSlot, task: Task) -> dict:
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
    }
