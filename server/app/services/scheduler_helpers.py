from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from typing import Dict, List

from app.models import Instrument
from app.models.models import SysCalendar


TIME_UNIT_MINUTES = 30
HORIZON_DAYS = 90
DEFAULT_WORK_START_MINUTES = 8 * 60 + 30
DEFAULT_WORK_END_MINUTES = 20 * 60


def natural_day_boundary(now: datetime, days: int) -> datetime:
    if days <= 0:
        return now
    next_boundary = datetime.combine(
        now.date() + timedelta(days=days),
        datetime.min.time(),
    )
    return next_boundary - timedelta(microseconds=1)


def time_horizon() -> tuple[datetime, datetime, int]:
    now = datetime.now().replace(second=0, microsecond=0)
    remaining_minutes = (-now.minute) % TIME_UNIT_MINUTES
    if remaining_minutes:
        now += timedelta(minutes=remaining_minutes)
    horizon_start = now
    horizon_end = now + timedelta(days=HORIZON_DAYS)
    total_units = int(
        (horizon_end - horizon_start).total_seconds()
        / 60
        / TIME_UNIT_MINUTES
    )
    return horizon_start, horizon_end, total_units


def to_units(hours: float) -> int:
    return max(1, math.ceil(hours * 60 / TIME_UNIT_MINUTES))


def datetime_to_units(dt: datetime, horizon_start: datetime) -> int:
    return int(
        (dt - horizon_start).total_seconds()
        / 60
        / TIME_UNIT_MINUTES
    )


def units_to_datetime(units: int, horizon_start: datetime) -> datetime:
    return horizon_start + timedelta(minutes=units * TIME_UNIT_MINUTES)


def parse_working_time(value, fallback_minutes: int) -> int:
    if isinstance(value, (int, float)):
        return _normalize_working_minutes(int(value * 60), fallback_minutes)
    if not isinstance(value, str):
        return fallback_minutes

    parts = value.split(":")
    if len(parts) != 2:
        return fallback_minutes
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        return fallback_minutes
    return _normalize_working_minutes(hours * 60 + minutes, fallback_minutes)


def working_time_bounds(params: dict | None) -> tuple[int, int]:
    params = params or {}
    start_minutes = parse_working_time(
        params.get("day_start"),
        DEFAULT_WORK_START_MINUTES,
    )
    end_minutes = parse_working_time(
        params.get("day_end"),
        DEFAULT_WORK_END_MINUTES,
    )
    if start_minutes >= end_minutes:
        return DEFAULT_WORK_START_MINUTES, DEFAULT_WORK_END_MINUTES
    return start_minutes, end_minutes


def build_compatibility(
    tasks,
    instruments,
    is_capability_matching_enabled: bool,
) -> Dict[int, List[Instrument]]:
    compatibility: Dict[int, List[Instrument]] = {}
    for task in tasks:
        if not task.requires_instrument:
            compatibility[task.id] = []
            continue

        instrument_ids = _parse_instrument_ids(task)
        if instrument_ids:
            compatibility[task.id] = [
                instrument
                for instrument in instruments
                if instrument.id in instrument_ids
            ]
            continue

        requirements = task.capability_requirements
        if not requirements or not is_capability_matching_enabled:
            compatibility[task.id] = list(instruments)
            continue

        required_tags = {
            (requirement.tag_name, requirement.tag_value)
            for requirement in requirements
        }
        compatibility[task.id] = [
            instrument
            for instrument in instruments
            if required_tags.issubset(
                {
                    (capability.tag_name, capability.tag_value)
                    for capability in instrument.capabilities
                }
            )
        ]
    return compatibility


def build_dependencies(
    tasks,
    children_by_parent: dict[int, list[int]] | None = None,
) -> list[tuple[int, int]]:
    children_by_parent = children_by_parent or {}
    dependencies = {
        (task.id, leaf_predecessor_id)
        for task in tasks
        for dependency in task.predecessors
        for leaf_predecessor_id in _effective_predecessor_ids(
            dependency.predecessor,
            children_by_parent,
        )
    }
    return sorted(dependencies)


def _effective_predecessor_ids(
    predecessor,
    children_by_parent: dict[int, list[int]],
) -> set[int]:
    if not getattr(predecessor, "is_external_gate", False):
        return _leaf_task_ids(predecessor.id, children_by_parent)
    bridged_ids = {
        task_id
        for dependency in predecessor.predecessors
        for task_id in _effective_predecessor_ids(
            dependency.predecessor,
            children_by_parent,
        )
    }
    return bridged_ids or {predecessor.id}


def _leaf_task_ids(
    task_id: int,
    children_by_parent: dict[int, list[int]],
) -> set[int]:
    pending_ids = [task_id]
    visited_ids = set()
    leaf_ids = set()
    while pending_ids:
        current_id = pending_ids.pop()
        if current_id in visited_ids:
            continue
        visited_ids.add(current_id)
        child_ids = children_by_parent.get(current_id, [])
        if child_ids:
            pending_ids.extend(child_ids)
        else:
            leaf_ids.add(current_id)
    return leaf_ids or {task_id}


def build_maintenance_windows(
    instruments,
    horizon_start: datetime,
) -> list[tuple[int, tuple[int, int]]]:
    windows = []
    for instrument in instruments:
        for window in instrument.maintenance_windows:
            start_unit = datetime_to_units(window.start_time, horizon_start)
            end_unit = datetime_to_units(window.end_time, horizon_start)
            if end_unit > 0:
                windows.append(
                    (instrument.id, (max(0, start_unit), end_unit))
                )
    return windows


def build_working_prefix_sum(
    horizon_start: datetime,
    total_units: int,
    day_start_minutes: int,
    day_end_minutes: int,
    maintenance_windows=None,
    calendar_days=None,
    include_weekends: bool = False,
    include_holidays: bool = False,
) -> list[int]:
    calendar_days = calendar_days or {}
    is_working = [1] * total_units
    for index in range(total_units):
        current = horizon_start + timedelta(
            minutes=index * TIME_UNIT_MINUTES
        )
        current_minutes = current.hour * 60 + current.minute
        if (
            current_minutes < day_start_minutes
            or current_minutes >= day_end_minutes
            or not is_allowed_calendar_day(
                current.date(),
                calendar_days,
                include_weekends,
                include_holidays,
            )
        ):
            is_working[index] = 0

    for _, (start_unit, end_unit) in maintenance_windows or []:
        for index in range(
            max(0, start_unit),
            min(end_unit, total_units),
        ):
            is_working[index] = 0

    prefix_sum = [0] * (total_units + 1)
    for index in range(total_units):
        prefix_sum[index + 1] = prefix_sum[index] + is_working[index]
    return prefix_sum


def is_allowed_calendar_day(
    current_date: date,
    calendar_days: dict[date, dict],
    include_weekends: bool,
    include_holidays: bool,
) -> bool:
    day = calendar_days.get(current_date)
    if day is None:
        return include_weekends if current_date.weekday() >= 5 else True

    day_type = day.get("day_type") or "workday"
    if day_type == "weekend":
        return include_weekends
    if day_type == "holiday":
        return include_holidays
    return bool(day.get("is_working_day", True))


def load_calendar_days(db, horizon_start: datetime, horizon_end: datetime) -> dict:
    rows = db.query(SysCalendar).filter(
        SysCalendar.date >= horizon_start.date(),
        SysCalendar.date <= horizon_end.date(),
    ).all()
    return {
        row.date: {
            "is_working_day": row.is_working_day,
            "day_type": row.day_type,
        }
        for row in rows
    }


def _parse_instrument_ids(task) -> list[int]:
    raw_ids = getattr(task, "instrument_ids", None)
    if not raw_ids:
        return []
    if isinstance(raw_ids, str):
        try:
            raw_ids = json.loads(raw_ids)
        except (json.JSONDecodeError, ValueError):
            raw_ids = [
                item.strip()
                for item in raw_ids.split(",")
                if item.strip()
            ]
    if isinstance(raw_ids, list):
        return [int(instrument_id) for instrument_id in raw_ids]
    return [int(raw_ids)]


def _normalize_working_minutes(value: int, fallback_minutes: int) -> int:
    if value < 0 or value > 24 * 60:
        return fallback_minutes
    if value % TIME_UNIT_MINUTES != 0:
        return fallback_minutes
    return value
