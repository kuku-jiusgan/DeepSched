"""Detect paused tasks whose saved schedule can no longer be resumed."""

from datetime import datetime

from app.models import Task
from app.services.task_progress_service import remaining_task_minutes


def mark_expired_paused_tasks(db, project_id: int, now: datetime | None = None) -> set[int]:
    """Mark paused tasks with remaining work and no future active slot as dirty.

    This deliberately changes only ``schedule_dirty``.  ``paused`` remains the
    execution state until a new schedule is generated and an operator resumes it.
    """
    checked_at = now or datetime.now()
    tasks = db.query(Task).filter(
        Task.project_id == project_id,
        Task.status == "paused",
        Task.is_external_gate.isnot(True),
    ).all()
    stale_ids: set[int] = set()
    for task in tasks:
        if _has_frozen_slot(task):
            continue
        if remaining_task_minutes(task) <= 0 or _has_open_execution(task):
            continue
        if _has_future_active_slot(task, checked_at):
            continue
        task.schedule_dirty = True
        stale_ids.add(task.id)
    return stale_ids


def _has_open_execution(task: Task) -> bool:
    return any(
        segment.ended_at is None for segment in task.execution_segments
    ) or any(
        slot.lifecycle_status == "active"
        and slot.actual_start is not None
        and slot.actual_end is None
        for slot in task.time_slots
    )


def _has_frozen_slot(task: Task) -> bool:
    return any(
        slot.lifecycle_status == "active" and slot.tier == "frozen"
        for slot in task.time_slots
    )


def _has_future_active_slot(task: Task, now: datetime) -> bool:
    return any(
        slot.lifecycle_status == "active"
        and slot.status in {"scheduled", "paused", "blocked", "interrupted"}
        and slot.plan_end is not None
        and slot.plan_end >= now
        for slot in task.time_slots
    )
