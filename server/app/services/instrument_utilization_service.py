from __future__ import annotations

from datetime import datetime, time, timedelta

from app.models import Instrument, InstrumentFault, TaskExecutionSegment, TaskNightRun, TimeSlot
from app.schemas.schemas import UtilizationStats
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import is_allowed_calendar_day, load_calendar_days, working_time_bounds


TimeRange = tuple[datetime, datetime]


def calculate_instrument_utilization(
    db,
    window_start: datetime,
    window_end: datetime,
    percent_scale: float = 100.0,
) -> list[UtilizationStats]:
    instruments = db.query(Instrument).filter(Instrument.availability_status == "available").all()
    working_ranges = _working_ranges(db, window_start, window_end)
    calendar_ranges = _calendar_ranges(window_start, window_end)
    result = []
    for instrument in instruments:
        available_ranges = _available_ranges(db, instrument.id, calendar_ranges, window_start, window_end)
        slots = _instrument_slots(db, instrument.id, window_start, window_end)
        actual_ranges = _actual_ranges(db, instrument.id, slots, window_start, window_end)
        night_ranges = _night_ranges(db, instrument.id, window_start, window_end)
        available_hours = _covered_hours(available_ranges)
        scheduled_hours = _hours_within(
            [(slot.plan_start, slot.plan_end) for slot in slots],
            available_ranges,
        )
        actual_hours = _hours_within(actual_ranges, working_ranges + night_ranges)
        result.append(UtilizationStats(
            instrument_id=instrument.id,
            instrument_name=instrument.name,
            instrument_code=instrument.code,
            total_available_hours=round(available_hours, 1),
            scheduled_hours=round(scheduled_hours, 1),
            actual_run_hours=round(actual_hours, 1),
            expected_utilization_rate=_rate(scheduled_hours, available_hours, percent_scale),
            actual_utilization_rate=min(_rate(actual_hours, available_hours, percent_scale), percent_scale),
            utilization_rate=min(_rate(actual_hours, available_hours, percent_scale), percent_scale),
            buffer_consumed_rate=0,
        ))
    return result


def _calendar_ranges(window_start: datetime, window_end: datetime) -> list[TimeRange]:
    if window_end <= window_start:
        return []
    return [(window_start, window_end)]


def _working_ranges(db, window_start: datetime, window_end: datetime) -> list[TimeRange]:
    rule = get_solver_constraints(db)["working_hours"]
    params = rule.params or {}
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


def _available_ranges(
    db,
    instrument_id: int,
    working_ranges: list[TimeRange],
    window_start: datetime,
    window_end: datetime,
) -> list[TimeRange]:
    faults = db.query(InstrumentFault).filter(
        InstrumentFault.instrument_id == instrument_id,
        InstrumentFault.reported_at < window_end,
    ).all()
    fault_ranges = [
        (max(fault.reported_at, window_start), min(fault.resolved_at or window_end, window_end))
        for fault in faults
        if (fault.resolved_at or window_end) > window_start
    ]
    return _subtract_ranges(working_ranges, fault_ranges)


def _instrument_slots(db, instrument_id: int, window_start: datetime, window_end: datetime) -> list[TimeSlot]:
    return db.query(TimeSlot).filter(
        TimeSlot.instrument_id == instrument_id,
        TimeSlot.plan_end > window_start,
        TimeSlot.plan_start < window_end,
    ).all()


def _actual_ranges(
    db,
    instrument_id: int,
    slots: list[TimeSlot],
    window_start: datetime,
    window_end: datetime,
) -> list[TimeRange]:
    segments = db.query(TaskExecutionSegment).filter(
        TaskExecutionSegment.instrument_id == instrument_id,
        TaskExecutionSegment.started_at < window_end,
    ).all()
    slot_by_id = {slot.id: slot for slot in slots}
    missing_slot_ids = {segment.slot_id for segment in segments if segment.slot_id not in slot_by_id}
    if missing_slot_ids:
        slot_by_id.update({slot.id: slot for slot in db.query(TimeSlot).filter(TimeSlot.id.in_(missing_slot_ids)).all()})
    ranges = [
        (
            max(segment.started_at, window_start),
            min(
                segment.ended_at or window_end,
                slot_by_id[segment.slot_id].plan_end if segment.slot_id in slot_by_id else window_end,
                window_end,
            ),
        )
        for segment in segments
        if (segment.ended_at or window_end) > window_start
        and segment.started_at < window_end
    ]
    segmented_slot_ids = {segment.slot_id for segment in segments}
    ranges.extend(
        (
            max(slot.actual_start, slot.plan_start),
            min(slot.actual_end or window_end, slot.plan_end),
        )
        for slot in slots
        if slot.id not in segmented_slot_ids
        and slot.status in {"completed", "running"}
        and slot.actual_start is not None
        and min(slot.actual_end or window_end, slot.plan_end) > max(slot.actual_start, slot.plan_start)
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


def _hours_within(ranges: list[TimeRange], boundaries: list[TimeRange]) -> float:
    intersections = []
    for start, end in ranges:
        for boundary_start, boundary_end in boundaries:
            overlap_start = max(start, boundary_start)
            overlap_end = min(end, boundary_end)
            if overlap_end > overlap_start:
                intersections.append((overlap_start, overlap_end))
    return _covered_hours(intersections)


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


def _subtract_ranges(ranges: list[TimeRange], exclusions: list[TimeRange]) -> list[TimeRange]:
    result = ranges
    for exclusion_start, exclusion_end in exclusions:
        remaining = []
        for start, end in result:
            if exclusion_end <= start or exclusion_start >= end:
                remaining.append((start, end))
                continue
            if exclusion_start > start:
                remaining.append((start, exclusion_start))
            if exclusion_end < end:
                remaining.append((exclusion_end, end))
        result = remaining
    return result


def _rate(hours: float, available_hours: float, percent_scale: float) -> float:
    if available_hours <= 0:
        return 0
    return round(min(hours / available_hours * percent_scale, percent_scale), 1)
