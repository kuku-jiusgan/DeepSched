from __future__ import annotations

from datetime import datetime, time, timedelta

from app.models import TaskExecutionSegment, TaskNightRun, TimeSlot
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import is_allowed_calendar_day, load_calendar_days, working_time_bounds


TimeRange = tuple[datetime, datetime]


def project_actual_hours_map(db, projects) -> dict[int, float]:
    project_list = list(projects)
    task_project = {
        task.id: project.id
        for project in project_list
        for task in project.tasks
        if not task.children
    }
    totals = {project.id: 0.0 for project in project_list}
    if not task_project:
        return totals

    task_ids = set(task_project)
    task_totals = task_actual_hours_map(db, task_ids)
    for task_id, hours in task_totals.items():
        totals[task_project[task_id]] += hours
    return {project_id: round(hours, 2) for project_id, hours in totals.items()}


def task_actual_hours_map(db, task_ids) -> dict[int, float]:
    task_ids = set(task_ids)
    totals = {task_id: 0.0 for task_id in task_ids}
    if not task_ids:
        return totals
    segments = db.query(TaskExecutionSegment).filter(TaskExecutionSegment.task_id.in_(task_ids)).all()
    slots = db.query(TimeSlot).filter(TimeSlot.task_id.in_(task_ids)).all()
    night_runs = db.query(TaskNightRun).filter(TaskNightRun.task_id.in_(task_ids)).all()
    ranges_by_task = _actual_ranges_by_task(task_ids, segments, slots)
    all_ranges = [item for ranges in ranges_by_task.values() for item in ranges]
    if not all_ranges:
        return totals

    working_ranges = _working_ranges(
        db,
        min(start for start, _ in all_ranges),
        max(end for _, end in all_ranges),
    )
    night_ranges_by_task = _night_ranges_by_task(night_runs)
    for task_id, actual_ranges in ranges_by_task.items():
        allowed_ranges = working_ranges + night_ranges_by_task.get(task_id, [])
        totals[task_id] = _hours_within(actual_ranges, allowed_ranges)
    return {task_id: round(hours, 2) for task_id, hours in totals.items()}


def _actual_ranges_by_task(task_ids, segments, slots) -> dict[int, list[TimeRange]]:
    now = datetime.now()
    result = {task_id: [] for task_id in task_ids}
    segmented_slot_ids = set()
    for segment in segments:
        result[segment.task_id].append((segment.started_at, segment.ended_at or now))
        segmented_slot_ids.add(segment.slot_id)
    for slot in slots:
        if slot.id in segmented_slot_ids or slot.status not in {"completed", "running"} or not slot.actual_start:
            continue
        start = max(slot.actual_start, slot.plan_start)
        end = min(slot.actual_end or now, slot.plan_end)
        if end > start:
            result[slot.task_id].append((start, end))
    return result


def _night_ranges_by_task(night_runs) -> dict[int, list[TimeRange]]:
    result: dict[int, list[TimeRange]] = {}
    for night_run in night_runs:
        result.setdefault(night_run.task_id, []).append((night_run.started_at, night_run.ended_at))
    return result


def _working_ranges(db, window_start: datetime, window_end: datetime) -> list[TimeRange]:
    params = get_solver_constraints(db)["working_hours"].params or {}
    day_start_minutes, day_end_minutes = working_time_bounds(params)
    calendar_days = load_calendar_days(db, window_start, window_end)
    include_weekends = bool(params.get("include_weekends", False))
    include_holidays = bool(params.get("include_holidays", False))
    ranges = []
    current_date = window_start.date()
    while current_date <= window_end.date():
        if is_allowed_calendar_day(current_date, calendar_days, include_weekends, include_holidays):
            day = datetime.combine(current_date, time.min)
            start = max(window_start, day + timedelta(minutes=day_start_minutes))
            end = min(window_end, day + timedelta(minutes=day_end_minutes))
            if end > start:
                ranges.append((start, end))
        current_date += timedelta(days=1)
    return ranges


def _hours_within(ranges: list[TimeRange], boundaries: list[TimeRange]) -> float:
    overlaps = []
    for start, end in ranges:
        for boundary_start, boundary_end in boundaries:
            overlap_start = max(start, boundary_start)
            overlap_end = min(end, boundary_end)
            if overlap_end > overlap_start:
                overlaps.append((overlap_start, overlap_end))
    if not overlaps:
        return 0
    ordered = sorted(overlaps)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return sum((end - start).total_seconds() / 3600 for start, end in merged)
