from __future__ import annotations

from sqlalchemy import case, func

from app.models import Task, TimeSlot
from app.services.scheduler_helpers import datetime_to_units


def load_missing_predecessor_ends(db, predecessor_ids: set[int], horizon_start) -> dict[int, int]:
    if not predecessor_ids:
        return {}
    # A completed task has released its resource at its actual end.  Do not
    # let a legacy completed slot without actual_end resurrect a future plan.
    effective_end = case(
        (Task.status.in_(("done", "completed")), TimeSlot.actual_end),
        else_=func.coalesce(TimeSlot.actual_end, TimeSlot.plan_end),
    )
    rows = db.query(
        TimeSlot.task_id,
        func.max(effective_end).label("max_end"),
    ).join(Task, Task.id == TimeSlot.task_id).filter(
        TimeSlot.task_id.in_(predecessor_ids),
        TimeSlot.lifecycle_status == "active",
    ).group_by(TimeSlot.task_id).all()
    return {
        task_id: max(0, datetime_to_units(max_end, horizon_start))
        for task_id, max_end in rows
        if max_end is not None
    }
