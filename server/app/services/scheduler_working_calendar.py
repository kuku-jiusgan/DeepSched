"""排程用的工作日历与有效工时前缀和。

前缀和让求解器可以用 AddElement 在常数时间内取到任意时刻之前累计的有效
工时，从而表达"跨度可以横跨夜间和周末，但跨度内的有效工时必须等于时长"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.instrument_working_time_service import load_working_time_context
from app.services.scheduler_helpers import (
    apply_maintenance_windows,
    build_working_flags,
    build_working_prefix_sum,
    prefix_sum_from_flags,
)


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
    calendar_days=None,
    rule_params=None,
    rule_enabled=None,
) -> WorkingCalendar:
    working_rule = constraints["working_hours"]
    working_params = working_rule.params or {}
    working_context = load_working_time_context(
        db, horizon_start, horizon_end, instruments, calendar_days=calendar_days,
        rule_params=rule_params, rule_enabled=rule_enabled,
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
    # 基础工作时段标记按策略缓存：26 台仪器通常只有一两种工作时段，逐台重算
    # 等于把整个视野（90 天 = 4320 个单元）的日期运算和日历查表做二十几遍。
    # 维护窗口是按仪器不同的，所以只缓存不含维护窗口的部分，每台仪器复制一份
    # 再叠加自己的窗口——**必须复制**，否则一台仪器的维护窗口会污染其他仪器。
    # include_weekends / include_holidays 沿用原先的全局策略取值，不改语义。
    base_flags_cache: dict[tuple[int, int, bool, bool], list[int]] = {}

    def _instrument_prefix_sum(instrument_id: int) -> list[int]:
        policy = working_context.policy_for(instrument_id)
        key = (
            policy.day_start_minutes,
            policy.day_end_minutes,
            include_weekends,
            include_holidays,
        )
        base = base_flags_cache.get(key)
        if base is None:
            base = build_working_flags(
                horizon_start,
                total_units,
                policy.day_start_minutes,
                policy.day_end_minutes,
                calendar_days,
                include_weekends,
                include_holidays,
            )
            base_flags_cache[key] = base
        flags = apply_maintenance_windows(
            list(base),
            total_units,
            [window for window in maint_windows if window[0] == instrument_id],
        )
        return prefix_sum_from_flags(flags, total_units)

    instrument_prefix_sums = {
        instrument.id: _instrument_prefix_sum(instrument.id)
        for instrument in instruments
    }
    return WorkingCalendar(
        context=working_context,
        params=working_params,
        calendar_days=calendar_days,
        global_prefix_sum=global_prefix_sum,
        instrument_prefix_sums=instrument_prefix_sums,
    )
