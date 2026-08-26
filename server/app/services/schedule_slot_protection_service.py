from __future__ import annotations

from datetime import datetime

from app.models import TimeSlot


def task_has_immovable_slot(db, task_id: int, at: datetime | None = None) -> bool:
    boundary = at or datetime.now()
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.lifecycle_status == "active",
    ).all()
    return any(
        (slot.actual_start is not None and slot.actual_end is None)
        or (
            slot.tier == "frozen"
            and slot.actual_start is None
            and slot.plan_end > boundary
        )
        for slot in slots
    )
