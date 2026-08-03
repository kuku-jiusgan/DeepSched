from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import Task, TimeSlot
from app.services.instrument_status_service import refresh_instrument_status
from app.services.schedule_advance_notification_service import notify_advanced_task_assignees
from app.services.schedule_forward_slot_service import build_forward_slots
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import (
    load_calendar_days,
    natural_day_boundary,
    time_horizon,
    working_time_bounds,
)
from app.services.project_status_service import calculate_project_status
from app.services.task_delay_status_service import mark_task_delayed
from app.services.schedule_delay_propagation_service import propagate_actual_delay
from app.services.schedule_delay_service import ScheduleDelayInvalidError

def complete_task_and_shift(
    db: Session,
    task_id: int,
    actual_end_time: datetime | None = None,
    completed_slot_id: int | None = None,
    release_instrument: bool = True,
) -> dict:
    end_time = actual_end_time or datetime.now()
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return {"status": "error", "message": "未找到指定任务"}

    task_slots = (
        db.query(TimeSlot)
        .filter(TimeSlot.task_id == task_id)
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    if not task_slots:
        return {"status": "error", "message": "任务没有排程时段"}

    planned_end = max(slot.plan_end for slot in task_slots)
    task.status = "done"
    if end_time > planned_end:
        mark_task_delayed(task)
    completed_slot = _select_completed_slot(task_slots, completed_slot_id, end_time)
    affected_instrument_ids = {slot.instrument_id for slot in task_slots if slot.instrument_id}
    _mark_task_slots_completed(task_slots, completed_slot, end_time)
    delay_result = _propagate_delay_safely(db, task, planned_end, end_time)
    if task.project:
        task.project.status = calculate_project_status(task.project)

    for instrument_id in affected_instrument_ids:
        refresh_instrument_status(db, instrument_id)

    db.flush()
    if not release_instrument:
        return {
            "status": "ok",
            "message": "任务已完成，未释放仪器，后续排程保持不变",
            "moved_tasks": 0,
            "released_instrument": False,
            "delayed_slots": delay_result["shifted_slots"],
            "delay_affected_tasks": delay_result["affected_tasks"],
        }
    if end_time > planned_end:
        db.flush()
        delay_warning = delay_result.get("warning")
        return {
            "status": "ok",
            "message": delay_warning or (
                f"任务已延期完成，已顺延 {delay_result['affected_tasks']} 个受影响任务"
            ),
            "moved_tasks": 0,
            "released_instrument": True,
            "delayed_slots": delay_result["shifted_slots"],
            "delay_affected_tasks": delay_result["affected_tasks"],
        }
    result = _forward_shift_instrument_queue(db, completed_slot.instrument_id, end_time)
    moved_task_details = result.pop("moved_task_details", [])
    notify_advanced_task_assignees(db, task, end_time, planned_end, moved_task_details)
    db.flush()
    result["released_instrument"] = True
    result["delayed_slots"] = delay_result["shifted_slots"]
    result["delay_affected_tasks"] = delay_result["affected_tasks"]
    return result


def _propagate_delay_safely(
    db: Session,
    task: Task,
    planned_end: datetime,
    actual_end: datetime,
) -> dict:
    try:
        with db.begin_nested():
            return propagate_actual_delay(db, task, planned_end, actual_end)
    except ScheduleDelayInvalidError as exc:
        return {
            "shifted_slots": 0,
            "affected_tasks": 0,
            "warning": f"任务已延期完成，但后续任务无法自动顺延：{exc}",
        }


def _select_completed_slot(
    slots: list[TimeSlot],
    completed_slot_id: int | None,
    end_time: datetime,
) -> TimeSlot:
    active_slot = next(
        (slot for slot in slots if slot.plan_start <= end_time <= slot.plan_end),
        None,
    )
    if active_slot:
        return active_slot

    started_slots = [slot for slot in slots if slot.plan_start <= end_time]
    if started_slots:
        return started_slots[-1]

    if completed_slot_id is not None:
        matched = next((slot for slot in slots if slot.id == completed_slot_id), None)
        if matched:
            return matched
    return slots[0]


def _mark_task_slots_completed(
    slots: list[TimeSlot],
    completed_slot: TimeSlot,
    end_time: datetime,
) -> None:
    for slot in slots:
        slot.status = "completed"
        if slot.plan_start > end_time:
            slot.actual_start = None
            slot.actual_end = None
            continue
        if slot.actual_start is None:
            slot.actual_start = slot.plan_start
        slot.actual_end = end_time if slot.id == completed_slot.id else min(slot.plan_end, end_time)


def _forward_shift_instrument_queue(
    db: Session,
    instrument_id: int | None,
    released_at: datetime,
) -> dict:
    if not instrument_id:
        return {
            "status": "ok",
            "message": "任务已完成，无绑定仪器，无需前移排程",
            "moved_tasks": 0,
        }

    candidate_tasks = _load_forward_shift_candidates(
        db,
        instrument_id,
        released_at,
    )
    if not candidate_tasks:
        return {
            "status": "ok",
            "message": "任务已完成，该仪器无后续任务可前移",
            "moved_tasks": 0,
        }

    working_options = _load_working_options(db, released_at)
    original_slots = {
        task.id: (
            db.query(TimeSlot)
            .filter(TimeSlot.task_id == task.id, TimeSlot.status == "scheduled")
            .order_by(TimeSlot.plan_start)
            .all()
        )
        for task in candidate_tasks
    }
    slot_snapshots = {
        task_id: [_snapshot_slot(slot) for slot in slots]
        for task_id, slots in original_slots.items()
    }

    for task_id in slot_snapshots:
        db.query(TimeSlot).filter(
            TimeSlot.task_id == task_id,
            TimeSlot.status == "scheduled",
        ).delete(synchronize_session="fetch")
    db.flush()

    moved = 0
    moved_task_details = []
    cursor = released_at
    for task in candidate_tasks:
        snapshots = slot_snapshots[task.id]
        if not snapshots:
            continue

        original_start = snapshots[0]["plan_start"]
        original_end = snapshots[-1]["plan_end"]
        duration_minutes = sum(
            int((slot["plan_end"] - slot["plan_start"]).total_seconds() / 60)
            for slot in snapshots
        )
        slot_instrument_id = snapshots[0]["instrument_id"]
        earliest_start = max(
            cursor,
            _dependency_ready_time(db, task, released_at),
            task.earliest_start or released_at,
            task.project.start_date if task.project and task.project.start_date else released_at,
        )
        new_slots = build_forward_slots(
            db,
            task,
            slot_instrument_id,
            duration_minutes,
            earliest_start,
            working_options,
        )

        if not new_slots or new_slots[0][0] >= original_start:
            _restore_slot_snapshots(db, snapshots)
            cursor = max(cursor, original_end)
            continue

        for start, end in new_slots:
            db.add(
                TimeSlot(
                    task_id=task.id,
                    schedule_run_id=snapshots[0].get("schedule_run_id", "legacy"),
                    instrument_id=slot_instrument_id,
                    plan_start=start,
                    plan_end=end,
                    tier=_tier_for_start(db, start),
                    status="scheduled",
                )
            )
        cursor = new_slots[-1][1]
        moved += 1
        moved_task_details.append({
            "task_id": task.id,
            "original_start": original_start,
            "original_end": original_end,
            "new_start": new_slots[0][0],
            "new_end": new_slots[-1][1],
        })

    db.flush()
    return {
        "status": "ok",
        "message": f"任务已完成，该仪器跨项目前移 {moved} 个任务",
        "moved_tasks": moved,
        "moved_task_details": moved_task_details,
    }


def _load_forward_shift_candidates(
    db: Session,
    instrument_id: int,
    released_at: datetime,
) -> list[Task]:
    first_start = func.min(TimeSlot.plan_start).label("first_start")
    candidate_rows = (
        db.query(Task.id, first_start)
        .join(TimeSlot, TimeSlot.task_id == Task.id)
        .filter(
            Task.status == "scheduled",
            TimeSlot.instrument_id == instrument_id,
            TimeSlot.status == "scheduled",
            TimeSlot.plan_start >= released_at,
        )
        .group_by(Task.id)
        .order_by(first_start, Task.id)
        .all()
    )
    candidate_ids = [row[0] for row in candidate_rows]
    tasks_by_id = {
        task.id: task
        for task in db.query(Task)
        .options(selectinload(Task.predecessors))
        .filter(Task.id.in_(candidate_ids))
        .all()
    }
    return [
        tasks_by_id[task_id]
        for task_id in candidate_ids
        if task_id in tasks_by_id
        and _is_movable_instrument_task(db, tasks_by_id[task_id], instrument_id, released_at)
    ]


def _is_movable_instrument_task(
    db: Session,
    task: Task,
    instrument_id: int,
    released_at: datetime,
) -> bool:
    slots = db.query(TimeSlot).filter(TimeSlot.task_id == task.id).all()
    return bool(slots) and all(
        slot.status == "scheduled"
        and slot.instrument_id == instrument_id
        and slot.plan_start >= released_at
        and slot.actual_start is None
        for slot in slots
    )


def _load_working_options(db: Session, released_at: datetime) -> dict:
    constraints = get_solver_constraints(db)
    working_rule = constraints["working_hours"]
    working_params = working_rule.params or {}
    day_start_minutes, day_end_minutes = working_time_bounds(working_params)
    include_weekends = bool(working_params.get("include_weekends", False))
    include_holidays = bool(working_params.get("include_holidays", False))
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
        "calendar_days": load_calendar_days(db, released_at, horizon_end),
    }


def _dependency_ready_time(
    db: Session,
    task: Task,
    fallback: datetime,
) -> datetime:
    ready_time = fallback
    for dependency in task.predecessors:
        pred_end = _predecessor_ready_time(db, dependency.predecessor_id)
        if pred_end and pred_end > ready_time:
            ready_time = pred_end
    return ready_time


def _predecessor_ready_time(db: Session, task_id: int) -> datetime | None:
    predecessor = db.query(Task).filter(Task.id == task_id).first()
    if predecessor and predecessor.status in {"done", "completed"}:
        actual_end = (
            db.query(func.max(TimeSlot.actual_end))
            .filter(TimeSlot.task_id == task_id)
            .scalar()
        )
        if actual_end:
            return actual_end
    return (
        db.query(func.max(TimeSlot.plan_end))
        .filter(TimeSlot.task_id == task_id)
        .scalar()
    )


def _snapshot_slot(slot: TimeSlot) -> dict:
    return {
        "task_id": slot.task_id,
        "schedule_run_id": slot.schedule_run_id,
        "instrument_id": slot.instrument_id,
        "plan_start": slot.plan_start,
        "plan_end": slot.plan_end,
        "tier": slot.tier,
        "status": slot.status,
    }


def _restore_slot_snapshots(db: Session, snapshots: list[dict]) -> None:
    for slot in snapshots:
        db.add(TimeSlot(**slot))


def _tier_for_start(db: Session, start: datetime) -> str:
    settings = get_settings()
    constraints = get_solver_constraints(db)
    freezing_rule = constraints["freezing"]
    freeze_days = int((freezing_rule.params or {}).get("freeze_days", settings.FROZEN_DAYS))
    now = datetime.now()
    if start <= natural_day_boundary(now, freeze_days):
        return "frozen"
    if start <= now + timedelta(days=settings.CONFIRMED_DAYS):
        return "confirmed"
    return "forecast"
