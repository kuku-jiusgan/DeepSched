from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import get_settings
from app.models import Task, TimeSlot
from app.services.scheduler_helpers import natural_day_boundary
from app.services.instrument_working_time_service import load_working_time_context
from app.services.schedule_rule_service import get_solver_constraints
from app.services.task_progress_service import remaining_task_minutes


def load_forward_shift_candidates(
    db,
    instrument_id: int | None,
    released_at: datetime,
    assignee_id: int | None = None,
) -> list[Task]:
    assignee_ids = _affected_assignee_ids(db, instrument_id, assignee_id, released_at)
    resource_filter = _resource_filter(instrument_id, assignee_ids)
    if resource_filter is None:
        return []
    rows = (
        db.query(Task, TimeSlot.plan_start)
        .join(TimeSlot, TimeSlot.task_id == Task.id)
        .filter(
            Task.status.in_(["pending", "scheduled", "blocked", "waiting_external"]),
            TimeSlot.status == "scheduled",
            TimeSlot.plan_end > released_at,
            TimeSlot.actual_start.is_(None),
            TimeSlot.lifecycle_status == "active",
        )
        .filter(resource_filter)
        .all()
    )
    tasks = {}
    first_starts = {}
    for task, plan_start in rows:
        tasks[task.id] = task
        first_starts[task.id] = min(first_starts.get(task.id, plan_start), plan_start)
    return sorted(tasks.values(), key=lambda task: (first_starts[task.id], task.id))


def _affected_assignee_ids(db, instrument_id: int | None, assignee_id: int | None, released_at: datetime) -> set[int]:
    assignee_ids = {assignee_id} if assignee_id is not None else set()
    if instrument_id is None:
        return assignee_ids
    rows = db.query(Task.assignee_id).join(TimeSlot, TimeSlot.task_id == Task.id).filter(
        TimeSlot.instrument_id == instrument_id,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
        TimeSlot.plan_end >= released_at,
        Task.assignee_id.isnot(None),
    ).distinct().all()
    assignee_ids.update(value for value, in rows)
    return assignee_ids


def _resource_filter(instrument_id: int | None, assignee_ids: set[int]):
    if instrument_id is not None:
        resource_filter = TimeSlot.instrument_id == instrument_id
        if assignee_ids:
            resource_filter = resource_filter | (
                Task.requires_human.is_(True) & Task.assignee_id.in_(assignee_ids)
            )
        return resource_filter
    if assignee_ids:
        return Task.requires_human.is_(True) & Task.assignee_id.in_(assignee_ids)
    return None


def is_movable_task(
    db,
    task: Task,
    instrument_id: int | None,
    released_at: datetime,
    assignee_id: int | None = None,
) -> bool:
    frozen = db.query(TimeSlot.id).filter(
        TimeSlot.task_id == task.id,
        TimeSlot.status == "scheduled",
        TimeSlot.tier == "frozen",
        TimeSlot.lifecycle_status == "active",
    ).first()
    if frozen:
        return False
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id == task.id,
        TimeSlot.status == "scheduled",
        TimeSlot.plan_start >= released_at,
        TimeSlot.actual_start.is_(None),
        TimeSlot.lifecycle_status == "active",
    ).all()
    return bool(slots) and all(
        (instrument_id is not None and slot.instrument_id == instrument_id)
        or (assignee_id is not None and task.assignee_id == assignee_id)
        for slot in slots
    )


def load_working_options(db, released_at: datetime) -> dict:
    context = load_working_time_context(db, released_at)
    policy = context.global_policy
    return {
        "day_start_minutes": policy.day_start_minutes,
        "day_end_minutes": policy.day_end_minutes,
        "include_weekends": policy.include_weekends,
        "include_holidays": policy.include_holidays,
        "horizon_end": context.horizon_end,
        "calendar_days": context.calendar_days,
        "working_time_context": context,
    }


def cross_project_setup_minutes(db) -> int:
    rule = get_solver_constraints(db)["cross_project_setup"]
    if not rule.is_enabled:
        return 0
    return max(0, round(float((rule.params or {}).get("setup_hours", 0.5)) * 60))


def dependency_ready_time(db, task: Task, fallback: datetime) -> datetime:
    ready_at = fallback
    for dependency in task.predecessors:
        predecessor_end = _predecessor_ready_time(db, dependency.predecessor_id)
        if predecessor_end and predecessor_end > ready_at:
            ready_at = predecessor_end
    return ready_at


def _predecessor_ready_time(db, task_id: int) -> datetime | None:
    predecessor = db.query(Task).filter(Task.id == task_id).first()
    if predecessor and predecessor.status in {"done", "completed"}:
        actual_end = max((
            value for value, in db.query(TimeSlot.actual_end).filter(
            TimeSlot.task_id == task_id,
            TimeSlot.lifecycle_status == "active",
            TimeSlot.actual_end.isnot(None),
        ).all()
        ), default=None)
        if actual_end:
            return actual_end
    return max((
        value for value, in db.query(TimeSlot.plan_end).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.lifecycle_status == "active",
    ).all()
    ), default=None)


def replan_duration_minutes(task: Task, slots: list[TimeSlot]) -> int:
    if task.est_duration_hours is not None:
        return remaining_task_minutes(task)
    return sum(
        int((slot.plan_end - slot.plan_start).total_seconds() / 60)
        for slot in slots
    )


def tier_for_start(db, start: datetime) -> str:
    settings = get_settings()
    rule = get_solver_constraints(db)["freezing"]
    freeze_days = int((rule.params or {}).get("freeze_days", settings.FROZEN_DAYS))
    now = datetime.now()
    if start <= natural_day_boundary(now, freeze_days):
        return "frozen"
    if start <= now + timedelta(days=settings.CONFIRMED_DAYS):
        return "confirmed"
    return "forecast"
