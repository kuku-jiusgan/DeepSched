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


def working_time_spans(
    db,
    start: datetime,
    end: datetime,
    instrument_id: int | None = None,
    context=None,
) -> list[tuple[datetime, datetime]]:
    """把一个时间窗口切成落在工作日历内的若干段。

    与 working_hours_between 是同一套逐日重叠的算法，只是把求和换成收集区间。
    用于甘特图显示：一个周五开始、周一才结束的时间槽在数据上保留完整起止，
    但画出来必须拆成"周五一段 + 周一一段"，中间的周末留空。
    """
    if end <= start:
        return []

    # context 由调用方按整批数据一次性建好传进来。不传就自己建——但那会为每一行
    # 重新查一遍排程规则、全部仪器和工作日历：甘特图一屏 77 个时间槽就是 77 遍，
    # 实测这一处占掉接口 946 毫秒里的绝大部分。
    context = context if context is not None else load_working_time_context(db, start, end)
    policy = context.policy_for(instrument_id)
    calendar_days = context.calendar_days

    spans: list[tuple[datetime, datetime]] = []
    current_date = start.date()
    while current_date <= end.date():
        if is_allowed_calendar_day(
            current_date,
            calendar_days,
            policy.include_weekends,
            policy.include_holidays,
        ):
            midnight = datetime.combine(current_date, time.min)
            overlap_start = max(start, midnight + timedelta(minutes=policy.day_start_minutes))
            overlap_end = min(end, midnight + timedelta(minutes=policy.day_end_minutes))
            if overlap_end > overlap_start:
                if spans and spans[-1][1] == overlap_start:
                    spans[-1] = (spans[-1][0], overlap_end)
                else:
                    spans.append((overlap_start, overlap_end))
        current_date += timedelta(days=1)
    return spans


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
    context=None,
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
        # 调用方可以传一份共用的上下文进来，省掉"每个任务重查一遍排程规则、
        # 全部仪器和工作日历"——待签批预测一次要为十几个任务铺工时，实测那一处
        # 占掉接口 439 毫秒里的大半。只有当它确实覆盖到本次要走的窗口时才复用，
        # 走出范围就照常重建，避免拿不全的日历算出错误的工作日。
        active = (
            context if context is not None and chunk_end <= context.horizon_end
            else load_working_time_context(db, cursor, chunk_end)
        )
        policy = active.policy_for(instrument_id)
        for _ in range(_CHUNK_DAYS):
            if remaining_seconds <= 0:
                return chunks
            window_start, window_end = _day_window(cursor, policy, active.calendar_days)
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
