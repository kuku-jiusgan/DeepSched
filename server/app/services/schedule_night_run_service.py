from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.models import TaskNightRun, TimeSlot
from app.services.instrument_status_service import (
    delete_time_slot_and_refresh,
    mark_instrument_running,
)
from app.domain.errors import DomainNotFoundError, DomainValidationError


class ScheduleNightRunNotFoundError(DomainNotFoundError):
    pass


class ScheduleNightRunInvalidError(DomainValidationError):
    pass


def record_night_run(
    db,
    slot_id: int,
    duration_hours: float,
    earliest_start: Optional[str],
    latest_end: Optional[str],
    operator_id: int | None = None,
) -> TimeSlot:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise ScheduleNightRunNotFoundError("时间槽不存在")
    if not slot.instrument_id:
        raise ScheduleNightRunInvalidError("该任务未绑定仪器，不能记录夜间运行")

    start_time = _resolve_night_start(slot, earliest_start)
    end_time = start_time + timedelta(hours=duration_hours)
    latest_end_time = _resolve_latest_end(start_time, latest_end)
    if latest_end_time and end_time > latest_end_time:
        raise ScheduleNightRunInvalidError("自动序列预计时长超过最晚结束时间")

    night_slot = _merge_or_create_night_slot(db, slot, start_time, end_time)
    _upsert_night_run(db, night_slot, start_time, end_time, operator_id)
    if night_slot.status == "running":
        mark_instrument_running(db, night_slot.instrument_id)
    db.flush()
    db.refresh(night_slot)
    return night_slot


def _upsert_night_run(
    db,
    slot: TimeSlot,
    start_time: datetime,
    end_time: datetime,
    operator_id: int | None,
) -> None:
    record = db.query(TaskNightRun).filter(
        TaskNightRun.task_id == slot.task_id,
        TaskNightRun.instrument_id == slot.instrument_id,
        TaskNightRun.started_at == start_time,
    ).first()
    if record:
        record.slot_id = slot.id
        record.ended_at = end_time
        record.operator_id = operator_id or record.operator_id
        return
    db.add(TaskNightRun(
        task_id=slot.task_id,
        slot_id=slot.id,
        instrument_id=slot.instrument_id,
        operator_id=operator_id,
        started_at=start_time,
        ended_at=end_time,
    ))


def _merge_or_create_night_slot(
    db,
    slot: TimeSlot,
    start_time: datetime,
    end_time: datetime,
) -> TimeSlot:
    existing_slot = db.query(TimeSlot).filter(
        TimeSlot.id != slot.id,
        TimeSlot.task_id == slot.task_id,
        TimeSlot.instrument_id == slot.instrument_id,
        TimeSlot.plan_start == start_time,
        TimeSlot.status.in_(["scheduled", "running"]),
    ).first()

    if start_time <= slot.plan_end:
        slot.plan_end = end_time
        slot.is_night_run = True
        if existing_slot:
            slot.plan_end = max(slot.plan_end, existing_slot.plan_end)
            delete_time_slot_and_refresh(db, existing_slot)
        return slot
    if existing_slot:
        existing_slot.plan_end = end_time
        existing_slot.is_night_run = True
        existing_slot.tier = slot.tier
        existing_slot.status = slot.status
        existing_slot.schedule_run_id = slot.schedule_run_id
        return existing_slot

    night_slot = TimeSlot(
        task_id=slot.task_id,
        schedule_run_id=slot.schedule_run_id,
        instrument_id=slot.instrument_id,
        plan_start=start_time,
        plan_end=end_time,
        is_night_run=True,
        tier=slot.tier,
        status=slot.status,
    )
    db.add(night_slot)
    db.flush()
    return night_slot


def _resolve_night_start(slot: TimeSlot, value: Optional[str]) -> datetime:
    fallback = _night_run_fallback_start(slot)
    parsed = _parse_clock_time_on_date(fallback, value)
    if parsed is None:
        return fallback
    if parsed > slot.plan_end:
        previous_day = parsed - timedelta(days=1)
        if previous_day >= slot.plan_start:
            parsed = previous_day
    while parsed < slot.plan_start:
        parsed += timedelta(days=1)
    return parsed


def _night_run_fallback_start(slot: TimeSlot) -> datetime:
    if slot.status == "running" and slot.actual_start:
        return slot.actual_start.replace(
            hour=slot.plan_end.hour,
            minute=slot.plan_end.minute,
            second=0,
            microsecond=0,
        )
    return slot.plan_end


def _resolve_latest_end(start_time: datetime, value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return _parse_clock_time(start_time, value)


def _parse_clock_time_on_date(base_time: datetime, value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    clean_value = value.strip().replace("次日", "").strip()
    return _replace_clock_time(base_time, clean_value)


def _parse_clock_time(base_time: datetime, value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    clean_value = value.strip()
    is_next_day = clean_value.startswith("次日")
    parsed = _replace_clock_time(base_time, clean_value.replace("次日", "").strip())
    if parsed is None:
        return None
    if is_next_day or parsed < base_time:
        parsed += timedelta(days=1)
    return parsed


def _replace_clock_time(base_time: datetime, value: str) -> Optional[datetime]:
    try:
        hour_text, minute_text = value.split(":", 1)
        return base_time.replace(
            hour=int(hour_text),
            minute=int(minute_text),
            second=0,
            microsecond=0,
        )
    except (TypeError, ValueError):
        return None
