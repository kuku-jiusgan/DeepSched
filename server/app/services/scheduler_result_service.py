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
from app.services.schedule_action_plan import (
    NotifySchedule,
    apply_schedule_notifications,
)
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
    """立即作废可替换的旧槽。排程主链路已改走指令集，这里保留给其它调用方。"""
    from app.services.schedule_action_plan import apply_supersedes

    apply_supersedes(db, plan_replaceable_supersedes(
        db, task_ids, reason, replaceable_after, preserved_slot_ids,
    ))


def plan_replaceable_supersedes(
    db,
    task_ids: set[int],
    reason: str,
    replaceable_after: datetime | None,
    preserved_slot_ids: set[int] | None = None,
) -> tuple:
    """选出这次重排里该被作废的旧时间槽，只出指令、不落盘。

    选择依赖库里的当前状态，所以是一次读；把它和"执行作废"分开之后，一份计划
    才能在执行之前被完整地看一眼。
    """
    from app.services.schedule_action_plan import SupersedeSlot

    if not task_ids:
        return ()
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.actual_start.is_(None),
        TimeSlot.actual_end.is_(None),
        TimeSlot.tier != "frozen",
        # 暂停任务未开始的时间槽同样可以被重排替换。只认 scheduled/running 的话，
        # 这些槽永远不会被作废，位置就钉死在原地，别的任务只能绕开它排——上面
        # 已经限定 actual_start 为空，真正跑过又被打断的那一段不会被动到。
        TimeSlot.status.in_(("scheduled", "running", "paused", "interrupted")),
    ).all()
    if replaceable_after is not None:
        # A slot crossing the replan boundary still reserves future capacity.
        # Keep only slots wholly finished before that boundary.
        slots = [slot for slot in slots if slot.plan_end > replaceable_after]
    preserved_slot_ids = preserved_slot_ids or set()
    return tuple(
        SupersedeSlot(slot.id, reason)
        for slot in sorted(slots, key=lambda item: item.id)
        if slot.id not in preserved_slot_ids
    )


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
    supersedes = plan_replaceable_supersedes(
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
    created, plan = persist_slots(
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
        supersedes=supersedes,
        notify=NotifySchedule(
            reason=advance_notification_reason,
            original_windows=original_schedule_windows or {},
        ) if emit_advance_notifications else None,
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
    # 通知排在一致性校验之后：校验不过会整体回滚，而发出去的通知收不回来。
    apply_schedule_notifications(db, plan)
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
