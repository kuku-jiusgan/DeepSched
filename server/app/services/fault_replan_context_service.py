from __future__ import annotations

from datetime import datetime

from app.models import TimeSlot


def build_fault_replan_context(
    db,
    task_ids: set[int],
    reported_at: datetime,
    estimated_resolved_at: datetime,
) -> dict:
    if not task_ids:
        return {
            "task_ids": set(),
            "remaining_duration_minutes": {},
            "earliest_start_bounds": {},
            "planning_start_at": reported_at,
            "replaceable_after": reported_at,
        }
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(("scheduled", "paused", "blocked", "interrupted")),
        TimeSlot.actual_start.is_(None),
        TimeSlot.plan_end > reported_at,
    ).all()
    remaining: dict[int, int] = {}
    for slot in slots:
        minutes = max(0, int((slot.plan_end - slot.plan_start).total_seconds() / 60))
        remaining[slot.task_id] = remaining.get(slot.task_id, 0) + minutes
    return {
        "task_ids": set(task_ids),
        "remaining_duration_minutes": remaining,
        "earliest_start_bounds": {task_id: estimated_resolved_at for task_id in task_ids},
        "planning_start_at": reported_at,
        "replaceable_after": reported_at,
    }
