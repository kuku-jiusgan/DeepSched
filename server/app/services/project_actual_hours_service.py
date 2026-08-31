from __future__ import annotations

from datetime import datetime, time, timedelta

from app.models import TaskExecutionSegment, TaskNightRun, TimeSlot
from app.services.instrument_working_time_service import load_working_time_context
from app.services.scheduler_helpers import is_allowed_calendar_day


TimeRange = tuple[datetime, datetime]
ResourceRange = tuple[datetime, datetime, int | None]


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
    night_runs = db.query(TaskNightRun).filter(
        TaskNightRun.task_id.in_(task_ids),
        TaskNightRun.lifecycle_status == "active",
    ).all()
    ranges_by_task = _actual_ranges_by_task(task_ids, segments, slots)
    all_ranges = [item for ranges in ranges_by_task.values() for item in ranges]
    if not all_ranges:
        return totals

    overall_start = min(start for start, _, _ in all_ranges)
    overall_end = max(end for _, end, _ in all_ranges)
    context = load_working_time_context(db, overall_start, overall_end)
    working_ranges: dict[int | None, list[TimeRange]] = {}
    night_ranges_by_task = _night_ranges_by_task(night_runs)
    for task_id, actual_ranges in ranges_by_task.items():
        hours = 0.0
        for start, end, instrument_id in actual_ranges:
            if instrument_id not in working_ranges:
                working_ranges[instrument_id] = _working_ranges(
                    context, overall_start, overall_end, instrument_id,
                )
            allowed_ranges = working_ranges[instrument_id] + night_ranges_by_task.get(task_id, [])
            hours += _hours_within([(start, end)], allowed_ranges)
        totals[task_id] = hours
    return {task_id: round(hours, 2) for task_id, hours in totals.items()}


def _actual_ranges_by_task(task_ids, segments, slots) -> dict[int, list[ResourceRange]]:
    now = datetime.now()
    result = {task_id: [] for task_id in task_ids}
    slots_by_id = {slot.id: slot for slot in slots}
    segmented_task_ids = set()
    for segment in segments:
        instrument_id = segment.instrument_id
        if instrument_id is None and segment.slot_id in slots_by_id:
            instrument_id = slots_by_id[segment.slot_id].instrument_id
        result[segment.task_id].append((segment.started_at, segment.ended_at or now, instrument_id))
        segmented_task_ids.add(segment.task_id)
    for slot in slots:
        # Execution segments are the authoritative source. Slot ranges are
        # retained only for legacy tasks that have no execution history.
        if slot.task_id in segmented_task_ids or slot.status not in {"completed", "running"} or not slot.actual_start:
            continue
        start = max(slot.actual_start, slot.plan_start)
        end = slot.actual_end or now
        if end > start:
            result[slot.task_id].append((start, end, slot.instrument_id))
    return result


def _night_ranges_by_task(night_runs) -> dict[int, list[TimeRange]]:
    result: dict[int, list[TimeRange]] = {}
    for night_run in night_runs:
        result.setdefault(night_run.task_id, []).append((night_run.started_at, night_run.ended_at))
    return result


def _working_ranges(context, window_start: datetime, window_end: datetime, instrument_id: int | None) -> list[TimeRange]:
    policy = context.policy_for(instrument_id)
    day_start_minutes = policy.day_start_minutes
    day_end_minutes = policy.day_end_minutes
    calendar_days = context.calendar_days
    ranges = []
    current_date = window_start.date()
    while current_date <= window_end.date():
        if is_allowed_calendar_day(
            current_date, calendar_days, policy.include_weekends, policy.include_holidays,
        ):
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
