"""求解成功后的落库：作废旧槽、写入新时间槽、一致性校验与变更通知。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from ortools.sat.python import cp_model

from app.models import TimeSlot
from app.services.schedule_advance_notification_service import (
    notify_rescheduled_tasks_advanced,
    notify_rescheduled_tasks_delayed,
)
from app.services.schedule_calendar_snapshot_service import save_schedule_calendar_snapshot
from app.services.schedule_conflict_service import ScheduleConflictError
from app.services.schedule_replan_validation_service import ensure_replan_consistent
from app.services.schedule_slot_change_log_service import supersede_slot
from app.services.scheduler_persistence import persist_slots
from app.services.instrument_working_time_service import serialize_instrument_policies


def new_schedule_run_id() -> str:
    return f"{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:8]}"


def supersede_replaceable_slots(
    db,
    task_ids: set[int],
    reason: str,
    replaceable_after: datetime | None,
    preserved_slot_ids: set[int] | None = None,
) -> None:
    if not task_ids:
        return
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.actual_start.is_(None),
        TimeSlot.actual_end.is_(None),
        TimeSlot.tier != "frozen",
        TimeSlot.status.in_(("scheduled", "running")),
    ).all()
    if replaceable_after is not None:
        # A slot crossing the replan boundary still reserves future capacity.
        # Keep only slots wholly finished before that boundary.
        slots = [slot for slot in slots if slot.plan_end > replaceable_after]
    preserved_slot_ids = preserved_slot_ids or set()
    for slot in slots:
        if slot.id in preserved_slot_ids:
            continue
        supersede_slot(db, slot, reason)
    db.flush()


def persist_schedule_result(
    db,
    *,
    solver,
    status,
    tasks,
    instruments,
    task_starts,
    task_ends,
    presences,
    split_unit_presences,
    horizon_start,
    horizon_end,
    working_context,
    working_params,
    calendar_days,
    maint_windows,
    freeze_days,
    forecast_task_ids,
    instrument_bridges,
    preserved_status_task_ids,
    preserved_slot_ids,
    replaceable_task_ids,
    replaceable_after,
    business_task_deps,
    queue_task_deps,
    original_schedule_windows,
    advance_notification_reason,
    emit_advance_notifications,
    rollback_on_conflict,
    commit,
) -> dict:
    """把求解结果落成时间槽，并返回排程接口的成功响应。"""
    # Persist results
    supersede_replaceable_slots(
        db,
        replaceable_task_ids or set(),
        "CP-SAT局部重排",
        replaceable_after,
        preserved_slot_ids,
    )
    schedule_run_id = new_schedule_run_id()
    save_schedule_calendar_snapshot(
        db,
        schedule_run_id,
        horizon_start,
        horizon_end,
        working_params,
        calendar_days,
        maint_windows,
        serialize_instrument_policies(working_context),
    )
    created = persist_slots(
        db,
        tasks,
        instruments,
        solver,
        task_starts,
        task_ends,
        presences,
        horizon_start,
        working_context,
        freeze_days,
        schedule_run_id,
        commit=False,
        split_unit_presences=split_unit_presences,
        forecast_task_ids=forecast_task_ids,
        instrument_bridges=instrument_bridges,
        preserved_status_task_ids=preserved_status_task_ids,
    )

    try:
        ensure_replan_consistent(
            db,
            schedule_run_id,
            business_task_deps,
            queue_task_deps,
        )
    except ScheduleConflictError as exc:
        if rollback_on_conflict:
            db.rollback()
        return {"status": "error", "message": str(exc), "timeslots_created": 0}
    if emit_advance_notifications:
        notify_rescheduled_tasks_advanced(
            db,
            original_schedule_windows,
            advance_notification_reason,
        )
        notify_rescheduled_tasks_delayed(
            db,
            original_schedule_windows,
            advance_notification_reason,
        )
    if commit:
        db.commit()

    return {
        "status": "ok",
        "message": f"排程完成",
        "timeslots_created": created,
        "schedule_run_id": schedule_run_id,
        "solver_status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
        "objective_value": int(solver.ObjectiveValue()),
    }
