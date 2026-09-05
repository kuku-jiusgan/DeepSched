from __future__ import annotations

from datetime import datetime, timedelta


from app.core.config import get_settings
from app.models import Task, TimeSlot
from app.services.scheduler_helpers import (
    TIME_UNIT_MINUTES,
    is_allowed_calendar_day,
    natural_day_boundary,
)
from app.services.schedule_action_plan import (
    SchedulePlan,
    ScheduleSlotPersistError,
    _slot_status,
    apply_schedule_plan,
    build_schedule_plan,
)
from app.services.schedule_slot_change_log_service import record_slot_created
from app.services.instrument_working_time_service import WorkingTimeContext

# 正在跑的任务位置不能动：它此刻真的在仪器上跑着，挪它等于篡改正在发生的事实。
IMMOVABLE_EXECUTION_STATUSES = {"running"}
# 暂停/中断只是"现在没在做"，位置照样要参与重排——后面的活要让路时它得跟着动。
# 必须保住的只是状态，不能被改写成待执行。
STATUS_PRESERVED_EXECUTION_STATUSES = {"paused", "interrupted"}
ACTIVE_EXECUTION_STATUSES = IMMOVABLE_EXECUTION_STATUSES | STATUS_PRESERVED_EXECUTION_STATUSES


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
    supersedes: tuple = (),
    notify=None,
    calendar_snapshot=None,
    base_epoch: int = 0,
) -> tuple[int, SchedulePlan]:
    """把求解结果落地，返回（新建槽数，这次执行的计划）。

    真正的两步在下面：先算出一份 SchedulePlan（纯值，不碰库），再执行它。分开之后
    "算出了什么"可以先看一眼、可以丢弃、可以序列化后交给别的进程，而不必像以前那样
    只能靠 savepoint 包住整段写操作再回滚。

    计划要交回给调用方，是因为里面的通知动作必须等一致性校验通过之后才能执行——
    校验不过会整体回滚，而已经发出去的通知回滚不掉。
    """
    now = datetime.now()
    plan = build_schedule_plan(
        tasks=tasks,
        instruments=instruments,
        solver=solver,
        task_starts=task_starts,
        task_ends=task_ends,
        presences=presences,
        split_unit_presences=split_unit_presences or {},
        horizon_start=horizon_start,
        working_context=working_context,
        schedule_run_id=schedule_run_id,
        supersedes=supersedes,
        notify=notify,
        calendar_snapshot=calendar_snapshot,
        base_epoch=base_epoch,
        frozen_boundary=natural_day_boundary(now, freeze_days),
        confirmed_boundary=now + timedelta(days=get_settings().CONFIRMED_DAYS),
        forecast_task_ids=forecast_task_ids or set(),
        preserved_status_task_ids=preserved_status_task_ids or set(),
        immovable_statuses=IMMOVABLE_EXECUTION_STATUSES,
        preserved_statuses=STATUS_PRESERVED_EXECUTION_STATUSES,
    )
    created = apply_schedule_plan(db, plan)
    if commit:
        db.commit()
    else:
        db.flush()
    return created, plan


def _persisted_task_status(task, is_preserved: bool) -> str:
    """时间槽落地时用的执行状态。真实实现已随指令集一起搬到 schedule_action_plan。"""
    return _slot_status(task, is_preserved)


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
    # 先落一次 flush 拿到主键，再写变更日志。原先是 add 完直接记录，此时
    # slot.id 还是 None——线上 1878 条新建记录里 1852 条的槽号是空的，等于这条
    # 日志指不回它记录的那个时间槽。
    db.flush()
    record_slot_created(db, slot, "replan")
    return 1
