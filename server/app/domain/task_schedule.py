from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol, TypeVar


class ScheduleSegment(Protocol):
    id: int
    plan_start: datetime
    plan_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    status: str


SegmentT = TypeVar("SegmentT", bound=ScheduleSegment)


def planned_task_window(segments: Iterable[ScheduleSegment]) -> tuple[datetime | None, datetime | None]:
    items = list(segments)
    return (
        min((item.plan_start for item in items if item.plan_start), default=None),
        max((item.plan_end for item in items if item.plan_end), default=None),
    )


def actual_task_window(segments: Iterable[ScheduleSegment]) -> tuple[datetime | None, datetime | None]:
    items = list(segments)
    return (
        min((item.actual_start for item in items if item.actual_start), default=None),
        max((item.actual_end for item in items if item.actual_end), default=None),
    )


def select_actionable_segment(segments: Iterable[SegmentT], now: datetime) -> SegmentT | None:
    items = sorted(segments, key=lambda item: (item.plan_start, item.id))
    running = next(
        (
            item for item in items
            if item.status == "running" and item.plan_end and item.plan_end >= now
        ),
        None,
    )
    if running:
        return running

    current_or_future = next(
        (
            item for item in items
            if item.status in {"scheduled", "blocked", "paused", "interrupted"}
            and item.plan_end
            and item.plan_end >= now
        ),
        None,
    )
    if current_or_future:
        return current_or_future

    return next((item for item in items if item.status != "completed"), items[-1] if items else None)
