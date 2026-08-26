from __future__ import annotations

from sqlalchemy import func

from app.models import TimeSlot
from app.services.scheduler_helpers import datetime_to_units


def load_missing_predecessor_ends(db, predecessor_ids: set[int], horizon_start) -> dict[int, int]:
    if not predecessor_ids:
        return {}
    effective_end = func.coalesce(TimeSlot.actual_end, TimeSlot.plan_end)
    rows = db.query(
        TimeSlot.task_id,
        func.max(effective_end).label("max_end"),
    ).filter(
        TimeSlot.task_id.in_(predecessor_ids),
        TimeSlot.lifecycle_status == "active",
    ).group_by(TimeSlot.task_id).all()
    return {
        task_id: max(0, datetime_to_units(max_end, horizon_start))
        for task_id, max_end in rows
        if max_end is not None
    }
