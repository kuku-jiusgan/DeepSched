from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.errors import DomainConflictError

from app.core.config import get_settings
from app.models import Task, TimeSlot
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations
from app.services.scheduler_helpers import (
    TIME_UNIT_MINUTES,
    is_allowed_calendar_day,
    natural_day_boundary,
)
from app.services.schedule_slot_change_log_service import record_slot_created
from app.services.instrument_working_time_service import WorkingTimeContext

# 正在跑的任务位置不能动：它此刻真的在仪器上跑着，挪它等于篡改正在发生的事实。
IMMOVABLE_EXECUTION_STATUSES = {"running"}
# 暂停/中断只是"现在没在做"，位置照样要参与重排——后面的活要让路时它得跟着动。
# 必须保住的只是状态，不能被改写成待执行。
STATUS_PRESERVED_EXECUTION_STATUSES = {"paused", "interrupted"}
ACTIVE_EXECUTION_STATUSES = IMMOVABLE_EXECUTION_STATUSES | STATUS_PRESERVED_EXECUTION_STATUSES


class ScheduleSlotPersistError(DomainConflictError):
    """排程结果落地时发现任务缺少仪器分配。"""


def persist_slots(
    db,
    tasks,
    instruments,
    solver,
    task_starts,
    task_ends,
    presences,
    horizon_start,
    working_context: WorkingTimeContext,
    freeze_days: int,
    schedule_run_id: str = "legacy",
    commit: bool = True,
    split_unit_presences=None,
    forecast_task_ids: set[int] | None = None,
    instrument_bridges: list[dict] | None = None,
    preserved_status_task_ids: set[int] | None = None,
) -> int:
    now = datetime.now()
    frozen_boundary = natural_day_boundary(now, freeze_days)
    confirmed_boundary = now + timedelta(
        days=get_settings().CONFIRMED_DAYS
    )
    created = 0
    split_unit_presences = split_unit_presences or {}
    forecast_task_ids = forecast_task_ids or set()
    preserved_status_task_ids = preserved_status_task_ids or set()
    for task in tasks:
        # 未签批方案的下游任务只参与求解以占用产能，不落地时间槽：签批之前
        # 不应该出现后续任务的排程结果。它们的求解位置仅用于判断项目能否
        # 按期完成，签批通过后会作为正常任务重新排程并落库。
        if task.id in forecast_task_ids:
            continue
        # Active execution slots are managed by task execution services; a new
        # schedule run must not replace their state with scheduled slots.
        is_preserved = (
            task.id in preserved_status_task_ids
            or task.status in STATUS_PRESERVED_EXECUTION_STATUSES
        )
        if task.status in IMMOVABLE_EXECUTION_STATUSES and not is_preserved:
            continue
        assigned_instrument = _assigned_instrument(task, instruments, solver, presences)
        if task.requires_instrument and assigned_instrument is None:
            # 求解模型对每个仪器任务都下了 AddExactlyOne，正常情况下必定分到一台
            # 仪器。走到这里说明这个任务压根没进模型，它的工时会凭空消失——旧代码
            # 在这里直接 continue，于是排程"成功"返回、任务的时间槽却一个都没有，
            # 剩余工时还挂在账上。这违背了"排不下必须当场报失败"的原则，宁可让
            # 整次排程回滚，也不能悄悄把活弄丢。
            raise ScheduleSlotPersistError(
                f"排程结果缺少任务【{task.name}】的仪器分配，本次排程未落地。"
                f"该任务仍有未完成工时，请重新排程或调整项目时间窗。"
            )

        if task.allow_split:
            created += _persist_split_task_slots(
                db,
                task,
                assigned_instrument,
                solver,
                split_unit_presences,
                horizon_start,
                frozen_boundary,
                confirmed_boundary,
                schedule_run_id,
                status=_persisted_task_status(task, is_preserved),
            )
            if not is_preserved:
                task.status = "scheduled"
            continue

        start_unit = solver.Value(task_starts[task.id])
        end_unit = solver.Value(task_ends[task.id])
        policy = working_context.policy_for(
            assigned_instrument.id if assigned_instrument else None,
        )
        chunk_start = None
        for unit in range(start_unit, end_unit):
            current = horizon_start + timedelta(
                minutes=unit * TIME_UNIT_MINUTES
            )
            current_minutes = current.hour * 60 + current.minute
            is_working = (
                policy.day_start_minutes <= current_minutes < policy.day_end_minutes
                and is_allowed_calendar_day(
                    current.date(),
                    working_context.calendar_days,
                    policy.include_weekends,
                    policy.include_holidays,
                )
            )
            if is_working and chunk_start is None:
                chunk_start = current
            elif not is_working and chunk_start is not None:
                created += _create_slot(
                    db,
                    task,
                    assigned_instrument,
                    chunk_start,
                    current,
                    frozen_boundary,
                    confirmed_boundary,
                    schedule_run_id,
                    status=_persisted_task_status(task, is_preserved),
                )
                chunk_start = None

        if chunk_start is not None:
            final_end = horizon_start + timedelta(
                minutes=end_unit * TIME_UNIT_MINUTES
            )
            created += _create_slot(
                db,
                task,
                assigned_instrument,
                chunk_start,
                final_end,
                frozen_boundary,
                confirmed_boundary,
                schedule_run_id,
                status=_persisted_task_status(task, is_preserved),
            )
        if not is_preserved:
            task.status = "scheduled"

    rebuild_instrument_bridge_reservations(db, schedule_run_id)

    if commit:
        db.commit()
    else:
        db.flush()
    return created


def _persisted_task_status(task, is_preserved: bool) -> str:
    if is_preserved and task.status != "running":
        return task.status
    return "scheduled"


def _persist_split_task_slots(
    db,
    task,
    instrument,
    solver,
    split_unit_presences,
    horizon_start,
    frozen_boundary,
    confirmed_boundary,
    schedule_run_id,
    status: str = "scheduled",
) -> int:
    selected_units = sorted(
        unit for (task_id, instrument_id, unit), presence in split_unit_presences.items()
        if task_id == task.id
        and instrument_id == instrument.id
        and solver.Value(presence) == 1
    )
    if not selected_units:
        return 0

    created = 0
    chunk_start = selected_units[0]
    previous_unit = selected_units[0]
    for unit in selected_units[1:]:
        if unit == previous_unit + 1:
            previous_unit = unit
            continue
        created += _create_slot(
            db,
            task,
            instrument,
            horizon_start + timedelta(minutes=chunk_start * TIME_UNIT_MINUTES),
            horizon_start + timedelta(minutes=(previous_unit + 1) * TIME_UNIT_MINUTES),
            frozen_boundary,
            confirmed_boundary,
            schedule_run_id,
            status=status,
        )
        chunk_start = unit
        previous_unit = unit

    created += _create_slot(
        db,
        task,
        instrument,
        horizon_start + timedelta(minutes=chunk_start * TIME_UNIT_MINUTES),
        horizon_start + timedelta(minutes=(previous_unit + 1) * TIME_UNIT_MINUTES),
        frozen_boundary,
        confirmed_boundary,
        schedule_run_id,
        status=status,
    )
    return created


def _assigned_instrument(task, instruments, solver, presences):
    if not task.requires_instrument:
        return None
    for instrument in instruments:
        key = (task.id, instrument.id)
        if key in presences and solver.Value(presences[key]) == 1:
            return instrument
    return None


def _create_slot(
    db,
    task,
    instrument,
    start,
    end,
    frozen_boundary,
    confirmed_boundary,
    schedule_run_id,
    status: str = "scheduled",
) -> int:
    if start <= frozen_boundary:
        tier = "frozen"
    elif start <= confirmed_boundary:
        tier = "confirmed"
    else:
        tier = "forecast"
    duplicate = db.query(TimeSlot.id).filter(
        TimeSlot.task_id == task.id,
        TimeSlot.instrument_id == (instrument.id if instrument else None),
        TimeSlot.plan_start == start,
        TimeSlot.plan_end == end,
        TimeSlot.status == status,
        TimeSlot.lifecycle_status == "active",
    ).first()
    if duplicate:
        return 0
    slot = TimeSlot(
            task_id=task.id,
            schedule_run_id=schedule_run_id,
            instrument_id=instrument.id if instrument else None,
            plan_start=start,
            plan_end=end,
            tier=tier,
            status=status,
        )
    db.add(slot)
    record_slot_created(db, slot, "replan")
    return 1
