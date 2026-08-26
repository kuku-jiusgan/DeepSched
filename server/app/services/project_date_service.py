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
    local_value = _to_local_naive(value)
    if local_value is None:
        return None
    return local_value.replace(hour=23, minute=59, second=59, microsecond=999999)


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
