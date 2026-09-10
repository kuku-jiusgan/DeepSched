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
    RecordCalendarSnapshot,
    SupersedeSlot,
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
    base_epoch: int = 0,
    released_slot_ids: set[int] | None = None,
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
    # 求解时被排除掉的那些槽，到这一步才真正作废——写回阶段的一条指令，而不是
    # 求解前的一次删除。
    # 只有实际进入本次落盘计划的任务，才能作废其旧槽。求解器可能因签批
    # 预测等原因把任务从落盘集合中剔除；此时保留旧槽，避免出现“旧槽已作废、
    # 新槽却没有生成”的数据丢失。
    persisted_task_ids = {task.id for task in tasks}
    releasable_ids = {
        slot_id for slot_id, task_id in db.query(
            TimeSlot.id, TimeSlot.task_id,
        ).filter(TimeSlot.id.in_(released_slot_ids or set())).all()
        if task_id in persisted_task_ids
    }
    # Forecast tasks are deliberately omitted from the CP-SAT variables, but
    # their old slots were still included in the released set by the resource
    # closure.  Supersede those stale slots as well; otherwise the solver can
    # place the selected task over an occupancy it never modeled.
    if forecast_task_ids and released_slot_ids:
        releasable_ids.update(
            slot_id for slot_id, task_id in db.query(
                TimeSlot.id, TimeSlot.task_id,
            ).filter(TimeSlot.id.in_(released_slot_ids)).all()
            if task_id in forecast_task_ids
        )
    superseded_ids = {action.slot_id for action in supersedes}
    supersedes = tuple(supersedes) + tuple(
        SupersedeSlot(slot_id, "排程重排")
        for slot_id in sorted(releasable_ids)
        if slot_id not in superseded_ids
    )
    schedule_run_id = new_schedule_run_id()
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
        base_epoch=base_epoch,
        calendar_snapshot=RecordCalendarSnapshot(
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            working_params=working_params,
            calendar_days=calendar_days,
            maintenance_windows=tuple(maint_windows),
            instrument_working_hours=serialize_instrument_policies(working_context),
        ),
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
