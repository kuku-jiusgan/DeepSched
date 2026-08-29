from __future__ import annotations

from datetime import datetime, time, timedelta

from app.services.instrument_working_time_service import load_working_time_context
from app.services.scheduler_helpers import (
    is_allowed_calendar_day,
    load_calendar_days,
    working_time_bounds,
)


# 单次工时推演最多跨越的自然日数，防止工作日历配置异常时无限循环。
MAX_ADVANCE_DAYS = 730
_CHUNK_DAYS = 90


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


def advance_working_hours(
    db,
    start: datetime,
    hours: float,
    instrument_id: int | None = None,
) -> datetime:
    """从 start 起前推 hours 个有效工作小时，返回结束时刻。

    是 working_hours_between 的逆运算，共用同一套工作日历与工作时段策略，
    保证与排程口径一致。project_completion_projection_service._advance_working_minutes
    是服务于另一套 options 入参的近似实现，两者互不依赖。
    """
    if hours <= 0:
        return start

    remaining_seconds = hours * 3600
    cursor = start
    walked_days = 0
    while walked_days < MAX_ADVANCE_DAYS:
        chunk_end = cursor + timedelta(days=_CHUNK_DAYS)
        context = load_working_time_context(db, cursor, chunk_end)
        policy = context.policy_for(instrument_id)
        for _ in range(_CHUNK_DAYS):
            window_start, window_end = _day_window(cursor, policy, context.calendar_days)
            if window_end is not None:
                available = (window_end - window_start).total_seconds()
                if available >= remaining_seconds:
                    return window_start + timedelta(seconds=remaining_seconds)
                remaining_seconds -= available
            cursor = datetime.combine(cursor.date(), time.min) + timedelta(days=1)
            walked_days += 1
    raise ValueError(f"工时推演超出 {MAX_ADVANCE_DAYS} 天上限，请检查工作日历配置")


def working_time_chunks(
    db,
    start: datetime,
    hours: float,
    instrument_id: int | None = None,
) -> list[tuple[datetime, datetime]]:
    """把 hours 个有效工时按天切成若干段，每段落在当天的工作时段内。

    advance_working_hours 只给出结束时刻，中间跨掉的夜间和周末是隐含的。要在
    甘特图上画出来就必须切段：一整段画过去会盖住周末，而排程本身是按工作日
    切成多个时间槽的，预测不切段就跟真实排程长得不一样。
    """
    if hours <= 0:
        return []
    remaining_seconds = hours * 3600
    cursor = start
    chunks: list[tuple[datetime, datetime]] = []
    walked_days = 0
    while walked_days < MAX_ADVANCE_DAYS and remaining_seconds > 0:
        chunk_end = cursor + timedelta(days=_CHUNK_DAYS)
        context = load_working_time_context(db, cursor, chunk_end)
        policy = context.policy_for(instrument_id)
        for _ in range(_CHUNK_DAYS):
            if remaining_seconds <= 0:
                return chunks
            window_start, window_end = _day_window(cursor, policy, context.calendar_days)
            if window_end is not None:
                available = (window_end - window_start).total_seconds()
                used = min(available, remaining_seconds)
                chunks.append((window_start, window_start + timedelta(seconds=used)))
                remaining_seconds -= used
            cursor = datetime.combine(cursor.date(), time.min) + timedelta(days=1)
            walked_days += 1
    if remaining_seconds > 0:
        raise ValueError(f"工时推演超出 {MAX_ADVANCE_DAYS} 天上限，请检查工作日历配置")
    return chunks


def working_time_flags(db, moment: datetime, instrument_ids) -> dict[int, bool]:
    """各仪器在给定时刻是否处于有效工作时段。

    一次性载入工作日历上下文再逐台判断，避免每台仪器各查一次库。
    """
    instrument_ids = list(instrument_ids)
    if not instrument_ids:
        return {}
    context = load_working_time_context(db, moment, moment + timedelta(days=1))
    flags: dict[int, bool] = {}
    for instrument_id in instrument_ids:
        try:
            policy = context.policy_for(instrument_id)
        except ValueError:
            flags[instrument_id] = False
            continue
        window_start, window_end = _day_window(moment, policy, context.calendar_days)
        flags[instrument_id] = window_end is not None and window_start <= moment < window_end
    return flags


def _day_window(cursor, policy, calendar_days) -> tuple[datetime, datetime | None]:
    """返回 cursor 当天剩余的可用工作时段；当天不可用时第二个元素为 None。"""
    if not is_allowed_calendar_day(
        cursor.date(),
        calendar_days,
        policy.include_weekends,
        policy.include_holidays,
    ):
        return cursor, None
    midnight = datetime.combine(cursor.date(), time.min)
    window_start = max(cursor, midnight + timedelta(minutes=policy.day_start_minutes))
    window_end = midnight + timedelta(minutes=policy.day_end_minutes)
    if window_end <= window_start:
        return cursor, None
    return window_start, window_end
