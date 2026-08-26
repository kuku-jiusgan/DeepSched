from __future__ import annotations

from app.models import Task, TimeSlot


ACTIVE_SLOT_STATUSES = {"scheduled", "running", "paused", "blocked", "interrupted"}
COMPLETED_TASK_STATUSES = {"done", "completed"}


def current_occupying_slot(
    db,
    instrument_id: int,
    excluded_task_id: int | None = None,
) -> TimeSlot | None:
    query = (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            TimeSlot.instrument_id == instrument_id,
            TimeSlot.lifecycle_status == "active",
            TimeSlot.actual_start.isnot(None),
            TimeSlot.actual_end.is_(None),
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
            ~Task.status.in_(COMPLETED_TASK_STATUSES),
        )
    )
    if excluded_task_id is not None:
        query = query.filter(Task.id != excluded_task_id)
    return query.order_by(
        TimeSlot.actual_start.desc(), TimeSlot.id.desc()
    ).first()
