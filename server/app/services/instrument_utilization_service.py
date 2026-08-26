from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from time import monotonic

from app.models import Instrument, InstrumentFault, TaskExecutionSegment, TaskNightRun, TimeSlot
from app.schemas.schemas import UtilizationStats
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import (
    is_allowed_calendar_day,
    load_calendar_days,
    working_time_bounds,
)


TimeRange = tuple[datetime, datetime]
_CACHE_TTL_SECONDS = 10.0
_cache_lock = Lock()
_utilization_cache: dict[tuple[int, datetime, datetime, float], tuple[float, list[UtilizationStats]]] = {}


def calculate_instrument_utilization(
    db,
    window_start: datetime,
    window_end: datetime,
    percent_scale: float = 100.0,
) -> list[UtilizationStats]:
    cache_key = (id(db.bind), window_start, window_end, percent_scale)
    now = monotonic()
    with _cache_lock:
        cached = _utilization_cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    instruments = db.query(Instrument).filter(Instrument.availability_status == "available").all()
    calendar_ranges = [(window_start, window_end)] if window_end > window_start else []
    result = []
    slot_map = _load_slots_by_instrument(db, window_start, window_end)
    segment_map = _load_segments_by_instrument(db, slot_map, window_start, window_end)
    night_map = _load_nights_by_instrument(db, window_start, window_end)
    fault_map = _load_faults_by_instrument(db, window_start, window_end)
    for instrument in instruments:
        effective_ranges = _effective_work_ranges(
            db, window_start, window_end, instrument.id,
        )
        slots = slot_map.get(instrument.id, [])
        actual_ranges = _actual_ranges_from_data(slots, segment_map.get(instrument.id, []), window_start, window_end)
        night_ranges = night_map.get(instrument.id, [])
        fault_ranges = fault_map.get(instrument.id, [])
        # 利用率分母是筛选窗口的自然总时长；只有分子按有效工作时段计数。
        available_hours = _covered_hours(calendar_ranges)
        scheduled_hours = _hours_within(
            [(slot.plan_start, slot.plan_end) for slot in slots],
            effective_ranges,
        )
        actual_ranges = _subtract_ranges([
            *_intersections(actual_ranges, effective_ranges),
            *night_ranges,
        ], fault_ranges)
        actual_hours = _covered_hours(actual_ranges)
        result.append(UtilizationStats(
            instrument_id=instrument.id,
            instrument_name=instrument.name,
            instrument_code=instrument.code,
            total_available_hours=round(available_hours, 1),
            scheduled_hours=round(scheduled_hours, 1),
            actual_run_hours=round(actual_hours, 1),
            expected_utilization_rate=_rate(scheduled_hours, available_hours, percent_scale),
            actual_utilization_rate=_rate(actual_hours, available_hours, percent_scale),
            utilization_rate=_rate(actual_hours, available_hours, percent_scale),
            buffer_consumed_rate=0,
        ))
    with _cache_lock:
        _utilization_cache[cache_key] = (monotonic(), result)
        if len(_utilization_cache) > 128:
            oldest_key = min(_utilization_cache, key=lambda key: _utilization_cache[key][0])
            _utilization_cache.pop(oldest_key, None)
    return result


def _load_slots_by_instrument(db, window_start, window_end):
    rows = db.query(TimeSlot).filter(
        TimeSlot.instrument_id.isnot(None),
        TimeSlot.plan_end > window_start,
        TimeSlot.plan_start < window_end,
    ).all()
    return _group_slots(rows)


def _group_slots(slots):
    grouped = {}
    for slot in slots:
        grouped.setdefault(slot.instrument_id, {})[slot.id] = slot
    return {instrument_id: list(items.values()) for instrument_id, items in grouped.items()}


def _load_segments_by_instrument(db, slot_map, window_start, window_end):
    slot_ids = [slot.id for slots in slot_map.values() for slot in slots]
    if not slot_ids:
        return {}
    rows = db.query(TaskExecutionSegment).filter(
        TaskExecutionSegment.slot_id.in_(slot_ids),
        TaskExecutionSegment.started_at < window_end,
        (TaskExecutionSegment.ended_at.is_(None) | (TaskExecutionSegment.ended_at > window_start)),
    ).all()
    instrument_by_slot = {slot.id: instrument_id for instrument_id, slots in slot_map.items() for slot in slots}
    grouped = {}
    for row in rows:
        instrument_id = instrument_by_slot.get(row.slot_id)
        if instrument_id is not None:
            grouped.setdefault(instrument_id, []).append(row)
    return grouped


def _load_nights_by_instrument(db, window_start, window_end):
    rows = db.query(TaskNightRun).filter(
        TaskNightRun.instrument_id.isnot(None),
        TaskNightRun.started_at < window_end,
        TaskNightRun.ended_at > window_start,
    ).all()
    return {instrument_id: [(max(row.started_at, window_start), min(row.ended_at, window_end)) for row in rows]
            for instrument_id, rows in _group_by_instrument(rows).items()}


def _load_faults_by_instrument(db, window_start, window_end):
    rows = db.query(InstrumentFault).filter(
        InstrumentFault.instrument_id.isnot(None),
        InstrumentFault.reported_at < window_end,
        (InstrumentFault.resolved_at.is_(None) | (InstrumentFault.resolved_at > window_start)),
    ).all()
    return {instrument_id: [(max(row.reported_at, window_start), min(row.resolved_at or window_end, window_end))
                            for row in rows if row.reported_at]
            for instrument_id, rows in _group_by_instrument(rows).items()}


def _group_by_instrument(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row.instrument_id, []).append(row)
    return grouped


def _actual_ranges_from_data(slots, segments, window_start, window_end):
    segmented_task_ids = {segment.task_id for segment in segments}
    ranges = [
        (max(segment.started_at, window_start), min(segment.ended_at or window_end, window_end))
        for segment in segments
    ]
    ranges.extend(
        (max(slot.actual_start, window_start), min(slot.actual_end or window_end, window_end))
        for slot in slots
        if slot.task_id not in segmented_task_ids
        and slot.actual_start is not None
        and (slot.actual_end or window_end) > window_start
    )
    return ranges


def _effective_work_ranges(
    db,
    window_start: datetime,
    window_end: datetime,
    instrument_id: int | None = None,
) -> list[TimeRange]:
    if window_end <= window_start:
        return []
    from app.services.instrument_working_time_service import load_working_time_context

    context = load_working_time_context(db, window_start, window_end)
    policy = context.policy_for(instrument_id)
    day_start, day_end = policy.day_start_minutes, policy.day_end_minutes
    include_weekends = policy.include_weekends
    include_holidays = policy.include_holidays
    calendar_days = context.calendar_days
    ranges: list[TimeRange] = []
    current_date = window_start.date()
    last_date = window_end.date()
    while current_date <= last_date:
        if is_allowed_calendar_day(
            current_date, calendar_days, include_weekends, include_holidays,
        ):
            day = datetime.combine(current_date, datetime.min.time())
            start = max(window_start, day + timedelta(minutes=day_start))
            end = min(window_end, day + timedelta(minutes=day_end))
            if end > start:
                ranges.append((start, end))
        current_date += timedelta(days=1)
    return ranges


def _instrument_slots(db, instrument_id: int, window_start: datetime, window_end: datetime) -> list[TimeSlot]:
    planned_slots = db.query(TimeSlot).filter(
        TimeSlot.instrument_id == instrument_id,
        TimeSlot.plan_end > window_start,
        TimeSlot.plan_start < window_end,
    ).all()
    actual_slots = db.query(TimeSlot).filter(
        TimeSlot.instrument_id == instrument_id,
        TimeSlot.actual_start.isnot(None),
        TimeSlot.actual_start < window_end,
    ).all()
    return list({
        slot.id: slot
        for slot in [*planned_slots, *actual_slots]
        if slot.plan_end > window_start
        or (slot.actual_start is not None and (slot.actual_end or window_end) > window_start)
    }.values())


def _actual_ranges(
    db,
    instrument_id: int,
    slots: list[TimeSlot],
    window_start: datetime,
    window_end: datetime,
) -> list[TimeRange]:
    slot_ids = [slot.id for slot in slots]
    task_ids = {slot.task_id for slot in slots}
    segments = db.query(TaskExecutionSegment).filter(
        TaskExecutionSegment.instrument_id == instrument_id,
        TaskExecutionSegment.task_id.in_(task_ids) if task_ids else False,
        TaskExecutionSegment.started_at < window_end,
        (TaskExecutionSegment.ended_at.is_(None) | (TaskExecutionSegment.ended_at > window_start)),
    ).all()
    segmented_task_ids = {segment.task_id for segment in segments}
    ranges = [
        (
            max(segment.started_at, window_start),
            min(segment.ended_at or window_end, window_end),
        )
        for segment in segments
    ]
    ranges.extend(
        (
            max(slot.actual_start, window_start),
            min(slot.actual_end or window_end, window_end),
        )
        for slot in slots
        if slot.task_id not in segmented_task_ids
        and slot.actual_start is not None
        and (slot.actual_end or window_end) > window_start
    )
    return ranges


def _night_ranges(
    db,
    instrument_id: int,
    window_start: datetime,
    window_end: datetime,
) -> list[TimeRange]:
    records = db.query(TaskNightRun).filter(
        TaskNightRun.instrument_id == instrument_id,
        TaskNightRun.started_at < window_end,
        TaskNightRun.ended_at > window_start,
    ).all()
    return [
        (max(record.started_at, window_start), min(record.ended_at, window_end))
        for record in records
    ]


def _fault_ranges(
    db,
    instrument_id: int,
    window_start: datetime,
    window_end: datetime,
) -> list[TimeRange]:
    faults = db.query(InstrumentFault).filter(
        InstrumentFault.instrument_id == instrument_id,
        InstrumentFault.reported_at < window_end,
        (InstrumentFault.resolved_at.is_(None) | (InstrumentFault.resolved_at > window_start)),
    ).all()
    return [
        (
            max(fault.reported_at, window_start),
            min(fault.resolved_at or window_end, window_end),
        )
        for fault in faults
        if fault.reported_at and (fault.resolved_at or window_end) > window_start
    ]


def _hours_within(ranges: list[TimeRange], boundaries: list[TimeRange]) -> float:
    return _covered_hours(_intersections(ranges, boundaries))


def _intersections(ranges: list[TimeRange], boundaries: list[TimeRange]) -> list[TimeRange]:
    intersections: list[TimeRange] = []
    for start, end in ranges:
        for boundary_start, boundary_end in boundaries:
            overlap_start = max(start, boundary_start)
            overlap_end = min(end, boundary_end)
            if overlap_end > overlap_start:
                intersections.append((overlap_start, overlap_end))
    return intersections


def _subtract_ranges(ranges: list[TimeRange], exclusions: list[TimeRange]) -> list[TimeRange]:
    remaining = list(ranges)
    for exclusion_start, exclusion_end in exclusions:
        next_ranges: list[TimeRange] = []
        for start, end in remaining:
            if exclusion_end <= start or exclusion_start >= end:
                next_ranges.append((start, end))
                continue
            if start < exclusion_start:
                next_ranges.append((start, exclusion_start))
            if exclusion_end < end:
                next_ranges.append((exclusion_end, end))
        remaining = next_ranges
    return remaining


def _covered_hours(ranges: list[TimeRange]) -> float:
    if not ranges:
        return 0
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return sum((end - start).total_seconds() / 3600 for start, end in merged)


def _rate(hours: float, available_hours: float, percent_scale: float) -> float:
    if available_hours <= 0:
        return 0
    return round(min(hours / available_hours * percent_scale, percent_scale), 1)
