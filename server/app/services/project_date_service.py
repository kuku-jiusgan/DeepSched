from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def normalize_project_start(value: datetime | None) -> datetime | None:
    local_value = _to_local_naive(value)
    if local_value is None:
        return None
    return local_value.replace(hour=0, minute=0, second=0, microsecond=0)


def normalize_project_end(value: datetime | None) -> datetime | None:
    """把结题日期归一到当天的最后一秒。

    这里不能带小数秒：end_date 是秒精度的 DATETIME 列，而 MySQL 默认对小数秒
    进位（sql_mode 未开 TIME_TRUNCATE_FRACTIONAL），23:59:59.999999 会被存成
    次日 00:00:00。那样界面上选「9 月 9 日」会显示成「9-10 00:00」，让人误以为
    截止日期是 10 号。排程粒度为 30 分钟，少这不到一秒不影响任何可用工时。
    """
    local_value = _to_local_naive(value)
    if local_value is None:
        return None
    return local_value.replace(hour=23, minute=59, second=59, microsecond=0)


def validate_project_window(
    start_date: datetime | None,
    end_date: datetime | None,
) -> None:
    if start_date and end_date and end_date < start_date:
        raise ValueError(
            f"项目结题日期不能早于开始日期（开始日期：{start_date:%Y-%m-%d}，"
            f"结题日期：{end_date:%Y-%m-%d}）"
        )


def _to_local_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
