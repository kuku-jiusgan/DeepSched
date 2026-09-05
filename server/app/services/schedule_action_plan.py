"""求解结果的动作指令集。

求解算完之后，"要往库里写什么"曾经和"怎么写"揉在一起：persist_slots 一边从求解
器读值、一边 db.add、一边改任务状态、一边重建桥接。结果是这份结果没法先看一眼再
决定要不要采纳——探测只能靠 savepoint 包住再回滚，而回滚拦不住已经发出去的通知，
也拦不住入口自带的提交。

这里把它劈成两半：

- ``build_schedule_plan`` 是纯函数，只从求解器的取值算出一份 SchedulePlan。它不
  碰数据库，可以被检查、被丢弃、被序列化后扔给另一个进程。
- ``apply_schedule_plan`` 负责落盘，而且**只调用系统里已有的落盘原语**
  （scheduler_persistence._create_slot 负责建槽、算 tier、去重、写变更日志；
  instrument_bridge_sync_service.rebuild_instrument_bridge_reservations 负责收尾
  重建派生表）。这里绝不手写属性比对，也绝不发原生 UPDATE——那种写法要覆盖时间槽
  的增删改、任务状态、变更日志、桥接预留、夜跑记录一大片关联表，漏一处就是脏数据，
  而这些规则已经在那些原语里了。

指令用 id 而不是实体：实体绑在会话上，一份计划就没法跨进程传，也没法先存下来
过一会儿再执行。执行时由这里按 id 取回实体，交给原语去写。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.scheduler_helpers import TIME_UNIT_MINUTES, is_allowed_calendar_day
from app.domain.errors import DomainConflictError


class ScheduleSlotPersistError(DomainConflictError):
    """排程结果落地时发现任务缺少仪器分配。"""


@dataclass(frozen=True)
class CreateSlot:
    """新建一个时间槽。tier 由落盘原语按冻结/确认边界自己算，这里不重复。"""

    task_id: int
    instrument_id: int | None
    plan_start: datetime
    plan_end: datetime
    status: str


@dataclass(frozen=True)
class SupersedeSlot:
    """作废一个旧时间槽。

    只带槽号和原因：作废本身牵动六件事（生命周期、执行状态、作废时间与原因、
    桥接预留失效、夜跑记录失效、变更日志），这些规则在 supersede_slot 这个既有
    原语里，指令不重复描述它们。
    """

    slot_id: int
    reason: str


@dataclass(frozen=True)
class SetTaskStatus:
    task_id: int
    status: str


@dataclass(frozen=True)
class NotifySchedule:
    """把这次重排里任务前移/后移的情况通知到人。

    这是整份计划里唯一对外可见的动作——通知发出去就收不回来了。它必须排在一致性
    校验通过之后执行，所以不能和写库动作一起在 apply_schedule_plan 里做完，
    执行入口单独是 apply_schedule_notifications。
    """

    reason: str
    original_windows: dict


@dataclass(frozen=True)
class SchedulePlan:
    """一次排程算出来的全部动作。纯值，可序列化。"""

    schedule_run_id: str
    # 冻结与确认边界属于"这份计划是什么时候算的"，随计划一起带走，落盘时才能算出
    # 与求解当时一致的 tier。
    frozen_boundary: datetime
    confirmed_boundary: datetime
    # 顺序有意义：先作废旧槽再建新槽。反过来的话建槽时的去重会撞上还没作废的
    # 旧槽，把本该新建的那一条判成重复而跳过。
    supersedes: tuple[SupersedeSlot, ...] = ()
    slots: tuple[CreateSlot, ...] = ()
    task_statuses: tuple[SetTaskStatus, ...] = ()
    # 通知是对外动作，发出去收不回来；探测跑的计划这里恒为 None。
    notify: NotifySchedule | None = None


def build_schedule_plan(
    *,
    tasks,
    instruments,
    solver,
    task_starts,
    task_ends,
    presences,
    split_unit_presences,
    horizon_start: datetime,
    working_context,
    schedule_run_id: str,
    supersedes: tuple[SupersedeSlot, ...],
    notify: NotifySchedule | None,
    frozen_boundary: datetime,
    confirmed_boundary: datetime,
    forecast_task_ids: set[int],
    preserved_status_task_ids: set[int],
    immovable_statuses: set[str],
    preserved_statuses: set[str],
) -> SchedulePlan:
    """从求解结果算出要执行的动作。不碰数据库。"""
    slots: list[CreateSlot] = []
    statuses: list[SetTaskStatus] = []
    for task in tasks:
        # 未签批方案的下游任务只参与求解以占用产能，不落地时间槽：签批之前不应该
        # 出现后续任务的排程结果。
        if task.id in forecast_task_ids:
            continue
        # 正在执行的时间槽由任务执行服务管理，一次重排不能把它们的状态改掉。
        is_preserved = (
            task.id in preserved_status_task_ids
            or task.status in preserved_statuses
        )
        if task.status in immovable_statuses and not is_preserved:
            continue
        instrument = _assigned_instrument(task, instruments, solver, presences)
        if task.requires_instrument and instrument is None:
            # 求解模型对每个仪器任务都下了 AddExactlyOne，正常情况下必定分到一台
            # 仪器。走到这里说明这个任务压根没进模型，它的工时会凭空消失。宁可让
            # 整次排程失败，也不能悄悄把活弄丢。
            raise ScheduleSlotPersistError(
                f"排程结果缺少任务【{task.name}】的仪器分配，本次排程未落地。"
                f"该任务仍有未完成工时，请重新排程或调整项目时间窗。"
            )
        status = _slot_status(task, is_preserved)
        if task.allow_split:
            slots.extend(_split_slots(
                task, instrument, solver, split_unit_presences, horizon_start, status,
            ))
        else:
            slots.extend(_continuous_slots(
                task, instrument, solver, task_starts, task_ends,
                horizon_start, working_context, status,
            ))
        if not is_preserved:
            statuses.append(SetTaskStatus(task.id, "scheduled"))
    return SchedulePlan(
        schedule_run_id=schedule_run_id,
        frozen_boundary=frozen_boundary,
        confirmed_boundary=confirmed_boundary,
        supersedes=tuple(supersedes),
        notify=notify,
        slots=tuple(slots),
        task_statuses=tuple(statuses),
    )


def apply_schedule_plan(db, plan: SchedulePlan) -> int:
    """执行一份计划，返回新建的时间槽数量。

    只调用既有落盘原语：作废走 supersede_slot（它负责收执行状态、失效桥接预留与
    夜跑记录、写变更日志），建槽走 _create_slot（它负责算 tier、去重、写变更日志），
    收尾走桥接预留的全量重建。
    """
    from app.models import Instrument, Task
    from app.services.instrument_bridge_sync_service import (
        rebuild_instrument_bridge_reservations,
    )
    from app.services.scheduler_persistence import _create_slot


    task_ids = {item.task_id for item in plan.slots} | {
        item.task_id for item in plan.task_statuses
    }
    tasks = {
        task.id: task
        for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
    } if task_ids else {}
    instrument_ids = {
        item.instrument_id for item in plan.slots if item.instrument_id is not None
    }
    instruments = {
        instrument.id: instrument
        for instrument in db.query(Instrument).filter(
            Instrument.id.in_(instrument_ids),
        ).all()
    } if instrument_ids else {}

    apply_supersedes(db, plan.supersedes)

    created = 0
    for action in plan.slots:
        created += _create_slot(
            db,
            tasks[action.task_id],
            instruments.get(action.instrument_id) if action.instrument_id else None,
            action.plan_start,
            action.plan_end,
            plan.frozen_boundary,
            plan.confirmed_boundary,
            plan.schedule_run_id,
            status=action.status,
        )
    for action in plan.task_statuses:
        tasks[action.task_id].status = action.status
    rebuild_instrument_bridge_reservations(db, plan.schedule_run_id)
    return created


def apply_supersedes(db, supersedes) -> None:
    """执行一批作废指令。走既有的 supersede_slot 原语。"""
    from app.models import TimeSlot
    from app.services.schedule_slot_change_log_service import supersede_slot

    if not supersedes:
        return
    by_id = {
        slot.id: slot
        for slot in db.query(TimeSlot).filter(
            TimeSlot.id.in_([item.slot_id for item in supersedes]),
        ).all()
    }
    for action in supersedes:
        slot = by_id.get(action.slot_id)
        if slot is not None:
            supersede_slot(db, slot, action.reason)
    db.flush()


def _slot_status(task, is_preserved: bool) -> str:
    if is_preserved and task.status != "running":
        return task.status
    return "scheduled"


def _assigned_instrument(task, instruments, solver, presences):
    if not task.requires_instrument:
        return None
    for instrument in instruments:
        key = (task.id, instrument.id)
        if key in presences and solver.Value(presences[key]) == 1:
            return instrument
    return None


def _at(horizon_start: datetime, unit: int) -> datetime:
    return horizon_start + timedelta(minutes=unit * TIME_UNIT_MINUTES)


def _split_slots(
    task, instrument, solver, split_unit_presences, horizon_start, status,
) -> list[CreateSlot]:
    """可分片任务：把求解器选中的时间单元合并成连续区间。"""
    selected = sorted(
        unit for (task_id, instrument_id, unit), presence in split_unit_presences.items()
        if task_id == task.id
        and instrument_id == instrument.id
        and solver.Value(presence) == 1
    )
    if not selected:
        return []
    instrument_id = instrument.id if instrument else None
    result: list[CreateSlot] = []
    chunk_start = previous = selected[0]
    for unit in selected[1:]:
        if unit == previous + 1:
            previous = unit
            continue
        result.append(CreateSlot(
            task.id, instrument_id,
            _at(horizon_start, chunk_start), _at(horizon_start, previous + 1), status,
        ))
        chunk_start = previous = unit
    result.append(CreateSlot(
        task.id, instrument_id,
        _at(horizon_start, chunk_start), _at(horizon_start, previous + 1), status,
    ))
    return result


def _continuous_slots(
    task, instrument, solver, task_starts, task_ends,
    horizon_start, working_context, status,
) -> list[CreateSlot]:
    """不可分片任务：跨度可以横跨夜间与休息日，但只在有效工作时段上落槽。"""
    instrument_id = instrument.id if instrument else None
    policy = working_context.policy_for(instrument_id)
    start_unit = solver.Value(task_starts[task.id])
    end_unit = solver.Value(task_ends[task.id])
    result: list[CreateSlot] = []
    chunk_start = None
    for unit in range(start_unit, end_unit):
        current = _at(horizon_start, unit)
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
            result.append(CreateSlot(
                task.id, instrument_id, chunk_start, current, status,
            ))
            chunk_start = None
    if chunk_start is not None:
        result.append(CreateSlot(
            task.id, instrument_id, chunk_start, _at(horizon_start, end_unit), status,
        ))
    return result


def apply_schedule_notifications(db, plan: SchedulePlan) -> None:
    """执行计划里的通知动作。

    单独一个入口，是因为它必须发生在一致性校验通过之后：校验不过会整体回滚，而
    已经发出去的通知回滚不掉。写库动作和对外动作之间隔着这道闸，不能揉在一起。
    """
    from app.services.schedule_advance_notification_service import (
        notify_rescheduled_tasks_advanced,
        notify_rescheduled_tasks_delayed,
    )

    if plan.notify is None:
        return
    notify_rescheduled_tasks_advanced(
        db, plan.notify.original_windows, plan.notify.reason,
    )
    notify_rescheduled_tasks_delayed(
        db, plan.notify.original_windows, plan.notify.reason,
    )
