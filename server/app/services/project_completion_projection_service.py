from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Project, Task, TimeSlot
from app.services.scheduler_helpers import is_allowed_calendar_day
from app.services.task_progress_service import remaining_task_minutes


FINISHED_TASK_STATUSES = {"done", "completed"}


def projected_project_completion(db, project: Project, options: dict) -> datetime:
    """Forecast completion of every unfinished leaf task in dependency order."""
    tasks = db.query(Task).filter(Task.project_id == project.id).order_by(
        Task.plan_order, Task.id,
    ).all()
    leaf_tasks = [task for task in tasks if not task.children]
    completion: dict[int, datetime] = {}
    pending = {task.id: task for task in leaf_tasks}
    baseline = project.start_date or datetime.now()

    while pending:
        progressed = False
        for task_id, task in list(pending.items()):
            predecessors = [dependency.predecessor for dependency in task.predecessors]
            if any(
                predecessor.id in pending
                and predecessor.status not in FINISHED_TASK_STATUSES
                for predecessor in predecessors
            ):
                continue
            dependency_end = max(
                (completion[item.id] for item in predecessors if item.id in completion),
                default=baseline,
            )
            completion[task_id] = _project_task_completion(db, task, dependency_end, options)
            pending.pop(task_id)
            progressed = True
        if not progressed:
            labels = "、".join(task.name for task in pending.values())
            raise ValueError(f"项目任务依赖无法完成推演：{labels}")

    return max(completion.values(), default=baseline)


def _project_task_completion(db, task: Task, dependency_end: datetime, options: dict) -> datetime:
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id == task.id,
        TimeSlot.lifecycle_status == "active",
    ).all()
    if task.status in FINISHED_TASK_STATUSES:
        ends = [slot.actual_end or slot.plan_end for slot in slots]
        return max(ends, default=dependency_end)
    if task.is_external_gate:
        gate_end = task.approved_at if task.gate_status == "approved" else task.expected_approval_at
        return max(dependency_end, gate_end) if gate_end else dependency_end

    unstarted_slots = [
        slot for slot in slots
        if slot.status in {"scheduled", "running", "paused", "blocked", "interrupted"}
        and slot.actual_end is None
    ]
    planned_start = min((slot.plan_start for slot in unstarted_slots), default=dependency_end)
    start = max(dependency_end, planned_start)
    duration = _forecast_minutes(task, unstarted_slots)
    instrument_ids = {
        slot.instrument_id for slot in unstarted_slots if slot.instrument_id is not None
    }
    if not instrument_ids and task.requires_instrument:
        instrument_ids = set(task.instrument_ids or [])
    if not instrument_ids:
        return _advance_working_minutes(start, duration, options)
    return min(
        _advance_working_minutes(start, duration, options, instrument_id)
        for instrument_id in instrument_ids
    )


def _forecast_minutes(task: Task, slots: list[TimeSlot]) -> int:
    if task.est_duration_hours is not None:
        return remaining_task_minutes(task)
    return sum(
        max(0, int((slot.plan_end - slot.plan_start).total_seconds() // 60))
        for slot in slots
    )


def _advance_working_minutes(
    start: datetime,
    minutes: int,
    options: dict,
    instrument_id: int | None = None,
) -> datetime:
    cursor = _ceil_to_half_hour(start)
    remaining = minutes
    while remaining > 0 and cursor < options["horizon_end"]:
        next_cursor = cursor + timedelta(minutes=30)
        if _is_working_unit(cursor, options, instrument_id):
            remaining -= 30
        cursor = next_cursor
    if remaining > 0:
        raise ValueError("项目剩余工期超出系统可规划范围")
    return cursor


def _is_working_unit(start: datetime, options: dict, instrument_id: int | None = None) -> bool:
    context = options.get("working_time_context")
    policy = context.policy_for(instrument_id) if context else None
    day_start = policy.day_start_minutes if policy else options["day_start_minutes"]
    day_end = policy.day_end_minutes if policy else options["day_end_minutes"]
    include_weekends = policy.include_weekends if policy else options["include_weekends"]
    include_holidays = policy.include_holidays if policy else options["include_holidays"]
    current_minutes = start.hour * 60 + start.minute
    return (
        day_start <= current_minutes < day_end
        and is_allowed_calendar_day(
            start.date(),
            options["calendar_days"],
            include_weekends,
            include_holidays,
        )
    )


def _ceil_to_half_hour(value: datetime) -> datetime:
    value = value.replace(second=0, microsecond=0)
    remainder = value.minute % 30
    return value if remainder == 0 else value + timedelta(minutes=30 - remainder)
