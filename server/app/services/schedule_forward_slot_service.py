from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import Instrument, InstrumentFault, MaintenanceWindow, Task, TimeSlot
from app.services.scheduler_helpers import is_allowed_calendar_day


SCHEDULE_UNIT_MINUTES = 30


def build_forward_slots(
    db: Session,
    task: Task,
    instrument_id: int,
    duration_minutes: int,
    earliest_start: datetime,
    working_options: dict,
    ignore_instrument_free_human_conflicts: bool = False,
) -> list[tuple[datetime, datetime]]:
    if not task.allow_split:
        return _build_contiguous_forward_slots(
            db,
            task,
            instrument_id,
            duration_minutes,
            earliest_start,
            working_options,
            ignore_instrument_free_human_conflicts,
        )

    remaining = duration_minutes
    slots: list[tuple[datetime, datetime]] = []
    chunk_start: datetime | None = None
    cursor = _ceil_to_schedule_unit(earliest_start)

    while remaining > 0 and cursor < working_options["horizon_end"]:
        next_cursor = cursor + timedelta(minutes=SCHEDULE_UNIT_MINUTES)
        if _can_use_unit(
            db,
            task,
            instrument_id,
            cursor,
            next_cursor,
            working_options,
            ignore_instrument_free_human_conflicts,
        ):
            if chunk_start is None:
                chunk_start = cursor
            remaining -= SCHEDULE_UNIT_MINUTES
        else:
            if chunk_start is not None:
                slots.append((chunk_start, cursor))
                chunk_start = None
        cursor = next_cursor

    if remaining <= 0 and chunk_start is not None:
        slots.append((chunk_start, cursor))
    if remaining > 0:
        return []
    return slots


def _build_contiguous_forward_slots(
    db: Session,
    task: Task,
    instrument_id: int,
    duration_minutes: int,
    earliest_start: datetime,
    working_options: dict,
    ignore_instrument_free_human_conflicts: bool,
) -> list[tuple[datetime, datetime]]:
    """Find one continuous logical execution window for a non-splittable task.

    Non-working periods (overnight/weekends) are allowed inside the window and
    are emitted as separate display ranges. Resource conflicts, however,
    invalidate the whole candidate window instead of splitting the task.
    """
    cursor = _ceil_to_schedule_unit(earliest_start)
    horizon_end = working_options["horizon_end"]
    while cursor < horizon_end:
        candidate = cursor
        remaining = duration_minutes
        ranges: list[tuple[datetime, datetime]] = []
        range_start: datetime | None = None
        conflict = False

        while remaining > 0 and candidate < horizon_end:
            next_candidate = candidate + timedelta(minutes=SCHEDULE_UNIT_MINUTES)
            if _is_working_unit(candidate, working_options):
                if not _can_use_unit(
                    db,
                    task,
                    instrument_id,
                    candidate,
                    next_candidate,
                    working_options,
                    ignore_instrument_free_human_conflicts,
                ):
                    conflict = True
                    cursor = next_candidate
                    break
                if range_start is None:
                    range_start = candidate
                remaining -= SCHEDULE_UNIT_MINUTES
            elif range_start is not None:
                ranges.append((range_start, candidate))
                range_start = None
            candidate = next_candidate

        if conflict:
            continue
        if remaining > 0:
            return []
        if range_start is not None:
            ranges.append((range_start, candidate))
        return ranges
    return []


def _is_working_unit(start: datetime, working_options: dict) -> bool:
    current_minutes = start.hour * 60 + start.minute
    return (
        working_options["day_start_minutes"] <= current_minutes < working_options["day_end_minutes"]
        and is_allowed_calendar_day(
            start.date(),
            working_options["calendar_days"],
            working_options["include_weekends"],
            working_options["include_holidays"],
        )
    )


def _can_use_unit(
    db: Session,
    task: Task,
    instrument_id: int,
    start: datetime,
    end: datetime,
    working_options: dict,
    ignore_instrument_free_human_conflicts: bool = False,
) -> bool:
    current_minutes = start.hour * 60 + start.minute
    if (
        current_minutes < working_options["day_start_minutes"]
        or current_minutes >= working_options["day_end_minutes"]
        or not is_allowed_calendar_day(
            start.date(),
            working_options["calendar_days"],
            working_options["include_weekends"],
            working_options["include_holidays"],
        )
    ):
        return False
    if _instrument_is_unavailable(
        db,
        instrument_id,
        start,
        end,
        working_options,
    ):
        return False
    instrument_conflict = (
        db.query(TimeSlot.id)
        .filter(
            TimeSlot.instrument_id == instrument_id,
            or_(
                and_(
                    TimeSlot.status == "completed",
                    TimeSlot.actual_start.isnot(None),
                    TimeSlot.actual_end.isnot(None),
                    TimeSlot.actual_start < end,
                    TimeSlot.actual_end > start,
                ),
                and_(
                    TimeSlot.status != "completed",
                    TimeSlot.plan_start < end,
                    TimeSlot.plan_end > start,
                ),
            ),
        )
        .first()
    )
    if instrument_conflict is not None:
        return False
    if not task.requires_human or task.assignee_id is None:
        return True
    human_conflict_query = (
        db.query(TimeSlot.id)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            Task.id != task.id,
            Task.requires_human.is_(True),
            Task.assignee_id == task.assignee_id,
            or_(
                and_(
                    TimeSlot.status == "completed",
                    TimeSlot.actual_start.isnot(None),
                    TimeSlot.actual_end.isnot(None),
                    TimeSlot.actual_start < end,
                    TimeSlot.actual_end > start,
                ),
                and_(
                    TimeSlot.status != "completed",
                    TimeSlot.plan_start < end,
                    TimeSlot.plan_end > start,
                ),
            ),
        )
    )
    if ignore_instrument_free_human_conflicts:
        human_conflict_query = human_conflict_query.filter(
            Task.requires_instrument.is_(True),
        )
    human_conflict = human_conflict_query.first()
    return human_conflict is None


def _instrument_is_unavailable(
    db: Session,
    instrument_id: int,
    start: datetime,
    end: datetime,
    working_options: dict,
) -> bool:
    cache = working_options.setdefault("instrument_unavailable_windows", {})
    if instrument_id not in cache:
        cache[instrument_id] = _load_instrument_unavailable_windows(db, instrument_id)
    return any(
        unavailable_start < end and unavailable_end > start
        for unavailable_start, unavailable_end in cache[instrument_id]
    )


def _load_instrument_unavailable_windows(
    db: Session,
    instrument_id: int,
) -> list[tuple[datetime, datetime]]:
    windows = [
        (window.start_time, window.end_time)
        for window in db.query(MaintenanceWindow).filter(
            MaintenanceWindow.instrument_id == instrument_id,
        ).all()
    ]
    faults = db.query(InstrumentFault).filter(
        InstrumentFault.instrument_id == instrument_id,
        InstrumentFault.status == "open",
    ).all()
    windows.extend(
        (
            fault.reported_at or datetime.min,
            fault.estimated_resolved_at or datetime.max,
        )
        for fault in faults
    )
    instrument_is_faulted = db.query(Instrument.id).filter(
        Instrument.id == instrument_id,
        Instrument.status == "fault",
    ).first() is not None
    if instrument_is_faulted and not faults:
        windows.append((datetime.min, datetime.max))
    return windows


def _ceil_to_schedule_unit(value: datetime) -> datetime:
    value = value.replace(second=0, microsecond=0)
    remainder = value.minute % SCHEDULE_UNIT_MINUTES
    if remainder == 0:
        return value
    value += timedelta(minutes=SCHEDULE_UNIT_MINUTES - remainder)
    return value.replace(second=0, microsecond=0)
