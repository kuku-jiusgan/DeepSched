from __future__ import annotations

from datetime import datetime

from app.models import Task, TimeSlot


MOVABLE_SLOT_STATUSES = ["scheduled", "blocked"]
MOVABLE_SLOT_TIERS = ["frozen", "confirmed", "forecast"]
MOVABLE_TASK_STATUSES = ["pending", "scheduled", "blocked"]


def anchor_schedule_end(db, task_id: int) -> datetime | None:
    slots = db.query(TimeSlot).filter(TimeSlot.task_id == task_id).all()
    return max((slot.plan_end for slot in slots), default=None)


def resource_queue_task_ids(
    db,
    selected_tasks: list[Task],
    anchor_end: datetime,
    excluded_task_ids: set[int],
) -> set[int]:
    instrument_ids = _selected_instrument_ids(db, selected_tasks)
    assignee_ids = {
        task.assignee_id
        for task in selected_tasks
        if task.requires_human and task.assignee_id is not None
    }
    if not instrument_ids and not assignee_ids:
        return set()

    candidate_ids = _resource_candidate_ids(
        db,
        instrument_ids,
        assignee_ids,
        anchor_end,
        excluded_task_ids,
    )
    candidates = db.query(Task).filter(Task.id.in_(candidate_ids)).all()
    return {
        task.id
        for task in candidates
        if task.status in MOVABLE_TASK_STATUSES
        and task.schedule_lock_status not in {"running", "completed"}
    }


def _selected_instrument_ids(db, selected_tasks: list[Task]) -> set[int]:
    selected_ids = {task.id for task in selected_tasks}
    scheduled_ids = {
        instrument_id
        for instrument_id, in db.query(TimeSlot.instrument_id).filter(
            TimeSlot.task_id.in_(selected_ids),
            TimeSlot.instrument_id.isnot(None),
        ).distinct().all()
    }
    configured_ids = {
        int(instrument_id)
        for task in selected_tasks
        for instrument_id in (task.instrument_ids or [])
    }
    return scheduled_ids or configured_ids


def _resource_candidate_ids(
    db,
    instrument_ids: set[int],
    assignee_ids: set[int],
    anchor_end: datetime,
    excluded_task_ids: set[int],
) -> set[int]:
    query = db.query(TimeSlot.task_id).join(Task).filter(
        ~TimeSlot.task_id.in_(excluded_task_ids),
        TimeSlot.plan_end > anchor_end,
        TimeSlot.actual_start.is_(None),
        TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
        TimeSlot.tier.in_(MOVABLE_SLOT_TIERS),
    )
    resource_filters = []
    if instrument_ids:
        resource_filters.append(TimeSlot.instrument_id.in_(instrument_ids))
    if assignee_ids:
        resource_filters.append(
            Task.requires_human.is_(True) & Task.assignee_id.in_(assignee_ids)
        )
    if len(resource_filters) == 1:
        query = query.filter(resource_filters[0])
    else:
        query = query.filter(resource_filters[0] | resource_filters[1])
    return {task_id for task_id, in query.distinct().all()}
