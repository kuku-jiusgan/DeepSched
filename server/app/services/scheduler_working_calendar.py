"""排程用的工作日历与有效工时前缀和。

前缀和让求解器可以用 AddElement 在常数时间内取到任意时刻之前累计的有效
工时，从而表达"跨度可以横跨夜间和周末，但跨度内的有效工时必须等于时长"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.instrument_working_time_service import load_working_time_context
from app.services.scheduler_helpers import build_working_prefix_sum


@dataclass(frozen=True)
class WorkingCalendar:
    context: Any
    params: dict
    calendar_days: Any
    global_prefix_sum: list[int]
    instrument_prefix_sums: dict[int, list[int]]


def build_working_calendar(
    db,
    *,
    instruments,
    constraints,
    horizon_start,
    horizon_end,
    total_units: int,
    maint_windows,
) -> WorkingCalendar:
    working_rule = constraints["working_hours"]
    working_params = working_rule.params or {}
    working_context = load_working_time_context(
        db, horizon_start, horizon_end, instruments,
    )
    global_policy = working_context.global_policy
    day_start_minutes = global_policy.day_start_minutes
    day_end_minutes = global_policy.day_end_minutes
    include_weekends = global_policy.include_weekends
    include_holidays = global_policy.include_holidays
    calendar_days = working_context.calendar_days
    global_prefix_sum = build_working_prefix_sum(
        horizon_start,
        total_units,
        day_start_minutes,
        day_end_minutes,
        [],
        calendar_days,
        include_weekends,
        include_holidays,
    )
    instrument_prefix_sums = {
        instrument.id: build_working_prefix_sum(
            horizon_start,
            total_units,
            working_context.policy_for(instrument.id).day_start_minutes,
            working_context.policy_for(instrument.id).day_end_minutes,
            [window for window in maint_windows if window[0] == instrument.id],
            calendar_days,
            include_weekends,
            include_holidays,
        )
        for instrument in instruments
    }
    return WorkingCalendar(
        context=working_context,
        params=working_params,
        calendar_days=calendar_days,
        global_prefix_sum=global_prefix_sum,
        instrument_prefix_sums=instrument_prefix_sums,
    )
