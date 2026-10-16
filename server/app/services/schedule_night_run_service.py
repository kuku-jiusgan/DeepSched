from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from math import isfinite

from app.models import TaskNightRun, TimeSlot
from app.domain.night_run import night_run_status
from app.repositories.task_execution_repository import lock_task
from app.repositories.workspace_repository import workspace_segments
from app.services.workspace_night_run_service import night_run_action
from app.services.instrument_status_service import (
    mark_instrument_running,
)
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations
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
    now: datetime | None = None,
) -> TimeSlot:
    current_time = now or datetime.now()
    slot = _night_run_source(db, slot_id, current_time)
    start_time, end_time = _night_run_window(slot, duration_hours, earliest_start, latest_end, current_time)

    night_slot = _merge_or_create_night_slot(db, slot, start_time, end_time)
    night_slot.status = night_run_status(start_time, end_time, current_time)
    _upsert_night_run(db, night_slot, start_time, end_time, operator_id)
    # Bridge reservations are derived from active task slots. Refresh them in
    # this transaction whenever a night-run slot changes the task timeline.
    rebuild_instrument_bridge_reservations(db, slot.schedule_run_id)
    if night_slot.status == "running":
        mark_instrument_running(db, night_slot.instrument_id)
    db.flush()
    db.refresh(night_slot)
    return night_slot


def _night_run_source(db, slot_id: int, current_time: datetime) -> TimeSlot:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise ScheduleNightRunNotFoundError("时间槽不存在")
    if not slot.instrument_id:
        raise ScheduleNightRunInvalidError("该任务未绑定仪器，不能记录夜间运行")
    task = lock_task(db, slot.task_id)
    if task is None:
        raise ScheduleNightRunNotFoundError("任务不存在")
    action = night_run_action(db, task, workspace_segments(task), current_time)
    if not action.is_eligible:
        raise ScheduleNightRunInvalidError(action.reason)
    if action.slot_id != slot.id:
        raise ScheduleNightRunInvalidError("夜间运行必须使用当天的仪器计划时段，请刷新工作台")

    return slot


def _night_run_window(
    slot: TimeSlot, duration_hours: float, earliest_start: str | None,
    latest_end: str | None, current_time: datetime,
) -> tuple[datetime, datetime]:
    if not isfinite(duration_hours) or duration_hours <= 0 or duration_hours * 2 != int(duration_hours * 2):
        raise ScheduleNightRunInvalidError("夜间运行时长必须为正数且以 0.5 小时为颗粒度")
    start_time = _resolve_night_start(slot, earliest_start)
    end_time = start_time + timedelta(hours=duration_hours)
    if start_time < slot.plan_end:
        raise ScheduleNightRunInvalidError("夜间运行开始时间不能早于当天计划结束时间")
    if end_time <= current_time:
        raise ScheduleNightRunInvalidError("夜间运行结束时间已过，请刷新工作台")
    latest_end_time = _resolve_latest_end(start_time, latest_end)
    if latest_end_time and end_time > latest_end_time:
        raise ScheduleNightRunInvalidError("自动序列预计时长超过最晚结束时间")

    return start_time, end_time


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
        record.lifecycle_status = "active"
        record.superseded_at = None
        record.superseded_reason = None
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
        TimeSlot.is_night_run.is_(True),
        TimeSlot.task_id == slot.task_id,
        TimeSlot.instrument_id == slot.instrument_id,
        TimeSlot.plan_start == start_time,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(["scheduled", "running"]),
    ).first()

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
        # 登记后的状态统一按夜跑区间计算，不继承白天时间槽的状态。
        status="scheduled",
    )
    db.add(night_slot)
    db.flush()
    return night_slot


def _resolve_night_start(slot: TimeSlot, value: Optional[str]) -> datetime:
    base_time = slot.plan_end
    parsed = _parse_clock_time_on_date(base_time, value)
    if parsed is None:
        if value:
            raise ScheduleNightRunInvalidError("夜间运行开始时间必须使用 HH:mm 格式")
        return base_time
    if parsed > slot.plan_end:
        previous_day = parsed - timedelta(days=1)
        if previous_day >= slot.plan_start:
            parsed = previous_day
    while parsed < slot.plan_start:
        parsed += timedelta(days=1)
    return parsed


def _resolve_latest_end(start_time: datetime, value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = _parse_clock_time(start_time, value)
    if parsed is None:
        raise ScheduleNightRunInvalidError("夜间运行最晚结束时间必须使用 HH:mm 格式")
    return parsed


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
