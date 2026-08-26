from __future__ import annotations

from datetime import datetime, time, timedelta

from app.services.instrument_working_time_service import load_working_time_context
from app.services.scheduler_helpers import (
    is_allowed_calendar_day,
    load_calendar_days,
    working_time_bounds,
)


def working_hours_between(
    db,
    start: datetime,
    end: datetime,
    instrument_id: int | None = None,
) -> float:
    if start == end:
        return 0.0
    if end < start:
        return -working_hours_between(db, end, start, instrument_id)

    context = load_working_time_context(db, start, end)
    policy = context.policy_for(instrument_id)
    day_start, day_end = policy.day_start_minutes, policy.day_end_minutes
    calendar_days = context.calendar_days

    total_seconds = 0.0
    current_date = start.date()
    while current_date <= end.date():
        if is_allowed_calendar_day(
            current_date,
            calendar_days,
            policy.include_weekends,
            policy.include_holidays,
        ):
            window_start = datetime.combine(current_date, time.min) + timedelta(minutes=day_start)
            window_end = datetime.combine(current_date, time.min) + timedelta(minutes=day_end)
            overlap_start = max(start, window_start)
            overlap_end = min(end, window_end)
            if overlap_end > overlap_start:
                total_seconds += (overlap_end - overlap_start).total_seconds()
        current_date += timedelta(days=1)
    return total_seconds / 3600
