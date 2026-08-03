from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable, Set

from app.models import AuditLog, Task, TaskDependency, TimeSlot
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.schedule_advance_notification_service import (
    capture_task_schedule_windows,
    notify_rescheduled_tasks_delayed,
)
from app.services.task_delay_status_service import mark_task_delayed
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import (
    is_allowed_calendar_day,
    load_calendar_days,
    time_horizon,
    working_time_bounds,
)
from app.domain.errors import DomainNotFoundError, DomainValidationError


class ScheduleDelayNotFoundError(DomainNotFoundError):
    pass


class ScheduleDelayInvalidError(DomainValidationError):
    pass


ACTIVE_SLOT_STATUSES = ["scheduled", "running", "blocked", "interrupted"]
ACTIVE_TASK_STATUSES = ["pending", "scheduled", "running", "blocked", "interrupted"]


def report_task_delay(db, slot_id: int, delay_hours: float, reason: str, operator_name: str = "system") -> dict:
    clean_reason = reason.strip()
    if delay_hours <= 0:
        raise ScheduleDelayInvalidError("延期时长必须大于 0")
    if not clean_reason:
        raise ScheduleDelayInvalidError("请填写异常原因")

    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise ScheduleDelayNotFoundError("时间槽不存在")

    task = db.query(Task).filter(Task.id == slot.task_id).first()
    if not task:
        raise ScheduleDelayNotFoundError("任务不存在")
    mark_task_delayed(task)

    final_slot = _final_task_slot(db, task.id)
    if not final_slot:
        raise ScheduleDelayNotFoundError("任务没有可延期的排程时段")

    slot = final_slot
    delay = timedelta(hours=delay_hours)
    cutoff = slot.plan_end
    affected_slot_ids = _affected_slot_ids(db, task, slot, cutoff)
    affected_task_ids = _task_ids_for_slots(db, affected_slot_ids)
    passive_task_ids = affected_task_ids - {task.id}
    original_windows = capture_task_schedule_windows(db, passive_task_ids)

    if task.status != "running" and slot.status != "running":
        slot.status = "blocked"
        task.status = "blocked"

    shifted_count = _apply_delay_with_working_hours(
        db,
        slot,
        affected_slot_ids - {slot.id},
        delay,
        cutoff,
    )
    notify_rescheduled_tasks_delayed(
        db,
        original_windows,
        f"任务“{task.name}”延期",
    )
    _write_audit_log(db, task.id, slot, delay_hours, clean_reason, shifted_count, operator_name)

    return {
        "status": "ok",
        "task_id": task.id,
        "slot_id": slot.id,
        "delay_hours": delay_hours,
        "shifted_slots": shifted_count,
        "affected_tasks": len(affected_task_ids),
        "reason": clean_reason,
    }


def _final_task_slot(db, task_id: int) -> TimeSlot | None:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == task_id,
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        )
        .order_by(TimeSlot.plan_end.desc(), TimeSlot.id.desc())
        .first()
    )


def _affected_slot_ids(db, task: Task, slot: TimeSlot, cutoff: datetime) -> Set[int]:
    slot_ids = {slot.id}
    slot_ids.update(_ids(_same_project_slots(db, task, cutoff)))
    if slot.instrument_id:
        slot_ids.update(_ids(_same_instrument_slots(db, slot, cutoff)))
    if task.requires_human and task.assignee_id is not None:
        slot_ids.update(_ids(_same_assignee_slots(db, task, cutoff)))
    slot_ids.update(_dependency_slot_ids(db, slot_ids, cutoff))
    return slot_ids


def _same_project_slots(db, task: Task, cutoff: datetime) -> Iterable[TimeSlot]:
    task_ids = db.query(Task.id).filter(
        Task.project_id == task.project_id,
        Task.status.in_(ACTIVE_TASK_STATUSES),
    )
    return db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        TimeSlot.plan_start >= cutoff,
    ).all()


def _same_instrument_slots(db, slot: TimeSlot, cutoff: datetime) -> Iterable[TimeSlot]:
    return db.query(TimeSlot).filter(
        TimeSlot.instrument_id == slot.instrument_id,
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        TimeSlot.plan_start >= cutoff,
    ).all()


def _same_assignee_slots(db, task: Task, cutoff: datetime) -> Iterable[TimeSlot]:
    task_ids = db.query(Task.id).filter(
        Task.id != task.id,
        Task.requires_human.is_(True),
        Task.assignee_id == task.assignee_id,
        Task.status.in_(ACTIVE_TASK_STATUSES),
    )
    return db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        TimeSlot.actual_start.is_(None),
        TimeSlot.plan_start >= cutoff,
    ).all()


def _dependency_slot_ids(db, slot_ids: set[int], cutoff: datetime) -> set[int]:
    task_ids = _task_ids_for_slots(db, slot_ids)
    descendants: set[int] = set()
    frontier = set(task_ids)
    while frontier:
        rows = db.query(TaskDependency.task_id).join(
            Task,
            Task.id == TaskDependency.task_id,
        ).filter(
            TaskDependency.predecessor_id.in_(frontier),
            Task.status.in_(ACTIVE_TASK_STATUSES),
        ).distinct().all()
        next_ids = {task_id for task_id, in rows} - task_ids - descendants
        descendants.update(next_ids)
        frontier = next_ids
    if not descendants:
        return set()
    rows = db.query(TimeSlot.id).filter(
        TimeSlot.task_id.in_(descendants),
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        TimeSlot.actual_start.is_(None),
        TimeSlot.plan_start >= cutoff,
    ).all()
    return {slot_id for slot_id, in rows}


def _apply_delay_with_working_hours(
    db,
    delayed_slot: TimeSlot,
    slot_ids: Set[int],
    delay: timedelta,
    cutoff: datetime,
) -> int:
    slots = (
        db.query(TimeSlot)
        .filter(TimeSlot.id.in_(slot_ids))
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
        if slot_ids else []
    )
    snapshots_by_task = _group_slot_snapshots(slots)
    if slot_ids:
        delete_time_slots_and_refresh(
            db,
            db.query(TimeSlot).filter(TimeSlot.id.in_(slot_ids)),
            synchronize_session="fetch",
        )

    options = _load_working_options(db, cutoff)
    delay_minutes = int(delay.total_seconds() / 60)
    delayed_end = _extend_delayed_task(db, delayed_slot, delay_minutes, options)
    _ensure_within_project_end(
        db.query(Task).filter(Task.id == delayed_slot.task_id).first(),
        delayed_end,
    )

    shifted_count = 0
    for snapshots in snapshots_by_task.values():
        first_slot = snapshots[0]
        duration_minutes = sum(
            int((snapshot["plan_end"] - snapshot["plan_start"]).total_seconds() / 60)
            for snapshot in snapshots
        )
        shifted_start = _advance_working_minutes(
            first_slot["plan_start"],
            delay_minutes,
            options,
        )
        ranges = _allocate_working_ranges(
            db,
            first_slot["instrument_id"],
            duration_minutes,
            shifted_start,
            options,
        )
        if not ranges:
            raise ScheduleDelayInvalidError("延期后的排程超出可规划范围")
        shifted_task = db.query(Task).filter(Task.id == first_slot["task_id"]).first()
        _ensure_within_project_end(shifted_task, ranges[-1][1])
        for start, end in ranges:
            db.add(TimeSlot(
                task_id=first_slot["task_id"],
                schedule_run_id=first_slot["schedule_run_id"],
                instrument_id=first_slot["instrument_id"],
                plan_start=start,
                plan_end=end,
                tier=first_slot["tier"],
                status=first_slot["status"],
            ))
        db.flush()
        shifted_count += len(snapshots)
    return shifted_count


def _extend_delayed_task(
    db,
    slot: TimeSlot,
    delay_minutes: int,
    options: dict,
) -> datetime:
    ranges = _allocate_working_ranges(
        db,
        slot.instrument_id,
        delay_minutes,
        slot.plan_end,
        options,
    )
    if not ranges:
        raise ScheduleDelayInvalidError("延期后的排程超出可规划范围")

    first_start, first_end = ranges[0]
    remaining_ranges = ranges
    if first_start == slot.plan_end:
        slot.plan_end = first_end
        remaining_ranges = ranges[1:]
    for start, end in remaining_ranges:
        db.add(TimeSlot(
            task_id=slot.task_id,
            schedule_run_id=slot.schedule_run_id,
            instrument_id=slot.instrument_id,
            plan_start=start,
            plan_end=end,
            tier=slot.tier,
            status=slot.status,
        ))
    db.flush()
    return ranges[-1][1]


def _ensure_within_project_end(task: Task | None, planned_end: datetime) -> None:
    if not task or not task.project or not task.project.end_date:
        return
    if planned_end <= task.project.end_date:
        return
    raise ScheduleDelayInvalidError(
        f"延期失败：项目【{task.project.name}】任务【{task.name}】顺延后预计结束时间 "
        f"{planned_end:%Y-%m-%d %H:%M}，超过项目结题时间 "
        f"{task.project.end_date:%Y-%m-%d %H:%M}。"
        "请缩短延期时长或先调整项目结题日期。"
    )


def _group_slot_snapshots(slots: list[TimeSlot]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for slot in slots:
        grouped[slot.task_id].append({
            "task_id": slot.task_id,
            "schedule_run_id": slot.schedule_run_id,
            "instrument_id": slot.instrument_id,
            "plan_start": slot.plan_start,
            "plan_end": slot.plan_end,
            "tier": slot.tier,
            "status": slot.status,
        })
    return dict(grouped)


def _load_working_options(db, start: datetime) -> dict:
    constraints = get_solver_constraints(db)
    working_rule = constraints["working_hours"]
    params = working_rule.params or {}
    day_start_minutes, day_end_minutes = working_time_bounds(params)
    include_weekends = bool(params.get("include_weekends", False))
    include_holidays = bool(params.get("include_holidays", False))
    if not working_rule.is_enabled:
        day_start_minutes, day_end_minutes = 0, 24 * 60
        include_weekends, include_holidays = True, True
    _, horizon_end, _ = time_horizon()
    return {
        "day_start_minutes": day_start_minutes,
        "day_end_minutes": day_end_minutes,
        "include_weekends": include_weekends,
        "include_holidays": include_holidays,
        "horizon_end": horizon_end,
        "calendar_days": load_calendar_days(db, start, horizon_end),
    }


def _advance_working_minutes(start: datetime, minutes: int, options: dict) -> datetime:
    cursor = _ceil_to_half_hour(start)
    remaining = minutes
    while remaining > 0 and cursor < options["horizon_end"]:
        next_cursor = cursor + timedelta(minutes=30)
        if _is_working_unit(cursor, options):
            remaining -= 30
        cursor = next_cursor
    return cursor


def _allocate_working_ranges(
    db,
    instrument_id: int | None,
    duration_minutes: int,
    earliest_start: datetime,
    options: dict,
) -> list[tuple[datetime, datetime]]:
    cursor = _ceil_to_half_hour(earliest_start)
    remaining = duration_minutes
    ranges: list[tuple[datetime, datetime]] = []
    range_start: datetime | None = None
    while remaining > 0 and cursor < options["horizon_end"]:
        next_cursor = cursor + timedelta(minutes=30)
        if _is_working_unit(cursor, options) and not _has_instrument_conflict(
            db, instrument_id, cursor, next_cursor
        ):
            if range_start is None:
                range_start = cursor
            remaining -= 30
        elif range_start is not None:
            ranges.append((range_start, cursor))
            range_start = None
        cursor = next_cursor
    if remaining <= 0 and range_start is not None:
        ranges.append((range_start, cursor))
    return ranges if remaining <= 0 else []


def _is_working_unit(start: datetime, options: dict) -> bool:
    current_minutes = start.hour * 60 + start.minute
    return (
        options["day_start_minutes"] <= current_minutes < options["day_end_minutes"]
        and is_allowed_calendar_day(
            start.date(),
            options["calendar_days"],
            options["include_weekends"],
            options["include_holidays"],
        )
    )


def _has_instrument_conflict(db, instrument_id: int | None, start: datetime, end: datetime) -> bool:
    if instrument_id is None:
        return False
    slots = db.query(TimeSlot).filter(TimeSlot.instrument_id == instrument_id).all()
    for slot in slots:
        if slot.status == "completed":
            if slot.actual_start and slot.actual_end and slot.actual_start < end and slot.actual_end > start:
                return True
            continue
        if slot.plan_start < end and slot.plan_end > start:
            return True
    return False


def _ceil_to_half_hour(value: datetime) -> datetime:
    value = value.replace(second=0, microsecond=0)
    remainder = value.minute % 30
    if remainder == 0:
        return value
    return value + timedelta(minutes=30 - remainder)


def _task_ids_for_slots(db, slot_ids: Set[int]) -> set[int]:
    if not slot_ids:
        return set()
    rows = db.query(TimeSlot.task_id).filter(TimeSlot.id.in_(slot_ids)).distinct().all()
    return {row[0] for row in rows}


def _write_audit_log(
    db,
    task_id: int,
    slot: TimeSlot,
    delay_hours: float,
    reason: str,
    shifted_count: int,
    operator_name: str,
) -> None:
    task = db.query(Task).filter(Task.id == task_id).first()
    task_display = _task_display(task)
    db.add(AuditLog(
        user_name=operator_name,
        action="task_delay_reported",
        target_type="time_slot",
        target_id=slot.id,
        detail={
            "task_id": task_id,
            "task_display": task_display,
            "schedule_run_id": slot.schedule_run_id,
            "delay_hours": delay_hours,
            "reason": reason,
            "shifted_slots": shifted_count,
        },
    ))


def _task_display(task: Task | None) -> str | None:
    if task is None:
        return None
    project = task.project
    if project is None:
        return task.name
    return " · ".join(part for part in [project.code, project.name, task.name] if part)


def _ids(slots: Iterable[TimeSlot]) -> Set[int]:
    return {slot.id for slot in slots}
