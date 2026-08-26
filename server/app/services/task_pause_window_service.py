from __future__ import annotations

from datetime import datetime

from app.models import Task, TimeSlot
from app.services.task_progress_service import remaining_task_minutes


CANDIDATE_SLOT_STATUSES = {"scheduled", "paused", "interrupted", "blocked"}


def instrument_queue_end(db, instrument_id: int | None, switch_time: datetime) -> datetime | None:
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
                TimeSlot.lifecycle_status == "active",
            ).all()
        ),
        default=None,
    )


def task_queue_slots(db, anchor: TimeSlot) -> list[TimeSlot]:
    slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == anchor.task_id,
            TimeSlot.instrument_id == anchor.instrument_id,
            TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
            TimeSlot.actual_end.is_(None),
            TimeSlot.lifecycle_status == "active",
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    if all(slot.id != anchor.id for slot in slots):
        slots.append(anchor)
    return sorted(slots, key=lambda slot: (slot.plan_start, slot.id))


def intermediate_task_slots(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    switch_time: datetime,
    queue_end: datetime,
) -> list[list[TimeSlot]]:
    instrument_slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.instrument_id == source_slot.instrument_id,
            TimeSlot.task_id.notin_([source_slot.task_id, target_slot.task_id]),
            TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_start >= switch_time,
            TimeSlot.plan_start < queue_end,
            TimeSlot.lifecycle_status == "active",
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    human_slots = _assignee_slots(db, source_slot, target_slot, switch_time, queue_end)
    slots = sorted(
        {slot.id: slot for slot in [*instrument_slots, *human_slots]}.values(),
        key=lambda slot: (slot.plan_start, slot.id),
    )
    task_ids = list(dict.fromkeys(slot.task_id for slot in slots))
    if not task_ids:
        return []
    all_slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id.in_(task_ids),
            TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_start >= switch_time,
            TimeSlot.lifecycle_status == "active",
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    groups: dict[int, list[TimeSlot]] = {task_id: [] for task_id in task_ids}
    for slot in all_slots:
        groups[slot.task_id].append(slot)
    return [groups[task_id] for task_id in task_ids]


def slot_minutes(slots: list[TimeSlot]) -> int:
    return sum(
        max(0, int((slot.plan_end - slot.plan_start).total_seconds() / 60))
        for slot in slots
    )


def remaining_minutes(
    task: Task,
    legacy_slots: list[TimeSlot] | None = None,
    switch_time: datetime | None = None,
    active_slot: TimeSlot | None = None,
) -> int:
    if task.est_duration_hours is None and legacy_slots is not None and switch_time and active_slot:
        return _remaining_slot_minutes(legacy_slots, switch_time, active_slot)
    return remaining_task_minutes(task)


def _assignee_slots(db, source_slot, target_slot, switch_time, queue_end) -> list[TimeSlot]:
    if not source_slot.task.requires_human or source_slot.task.assignee_id is None:
        return []
    return (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            Task.requires_human.is_(True),
            Task.assignee_id == source_slot.task.assignee_id,
            TimeSlot.task_id.notin_([source_slot.task_id, target_slot.task_id]),
            TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_start >= switch_time,
            TimeSlot.plan_start < queue_end,
            TimeSlot.lifecycle_status == "active",
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )


def _remaining_slot_minutes(slots: list[TimeSlot], from_time: datetime, active_slot: TimeSlot) -> int:
    minutes = 0
    for slot in slots:
        if slot.id == active_slot.id and slot.actual_start is None:
            start = slot.plan_start
        else:
            if slot.plan_end <= from_time:
                continue
            start = max(slot.plan_start, from_time)
        minutes += max(0, int((slot.plan_end - start).total_seconds() / 60))
    return minutes
