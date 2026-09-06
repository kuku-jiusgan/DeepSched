from __future__ import annotations

import logging
from datetime import datetime, timedelta
from math import ceil

from app.domain.errors import DomainConflictError
from app.models import TimeSlot
from app.services.resource_replan_service import replan_resource_closure
from app.services.schedule_slot_change_log_service import supersede_slot
from app.services.task_pause_switch_context_service import (
    PauseSwitchContext, build_pause_switch_context,
)


_logger = logging.getLogger(__name__)


def replan_pause_switch(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    started_at: datetime,
) -> datetime:
    """Replan the original switch window while preserving the start anchor.

    返回本次切换的时刻。锚点时间槽被压在这一刻上，接替任务必须以同一个时刻恢复，
    调用方不能自己再取一次当前时间。
    """
    context = build_pause_switch_context(db, source_slot, target_slot, started_at)
    savepoint = db.begin_nested()
    try:
        apply_switch_anchors(db, context, source_slot, target_slot)
        result = run_switch_replan(db, context)
        if result.get("status") == "ok":
            savepoint.commit()
            return context.switch_time
        savepoint.rollback()
    except Exception:
        if savepoint.is_active:
            savepoint.rollback()
        raise
    raise _switch_failure(db, source_slot, target_slot, started_at, result)


def apply_switch_anchors(
    db,
    context: PauseSwitchContext,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
) -> None:
    """把切换这一刻的既成事实压进时间槽，并作废要重排的那些槽。

    正式排程和失败诊断的复算都从这里开始：两者必须是同一道题，否则诊断说的
    就不是用户刚刚做的那件事。
    """
    _prepare_switch_anchors(source_slot, target_slot, context.switch_time)
    for slot in context.replaceable_slots:
        supersede_slot(db, slot, "暂停切换重排")
    db.flush()


def run_switch_replan(
    db,
    context: PauseSwitchContext,
    *,
    project_end_date_overrides: dict[int, datetime] | None = None,
    solver_time_limit: float = 8.0,
) -> dict:
    """Solve this switch with the authoritative replan entry."""
    return replan_resource_closure(
        db,
        context.task_ids,
        context.switch_time,
        context.current_project_id,
        earliest_start_bounds={task_id: context.switch_time for task_id in context.task_ids},
        advance_notification_reason="暂停切换重排",
        remaining_duration_minutes=context.remaining_duration_minutes,
        planning_start_at=context.switch_time,
        planning_end_at=solver_horizon_end(
            context.queue_end,
            context.remaining_duration_minutes,
        ),
        replaceable_after=context.switch_time,
        expand_closure=False,
        # 源任务和接替任务都要进保留名单。落地环节会把状态是运行中/已暂停/
        # 已中断、又不在名单里的任务整个跳过，一个时间槽都不落。接替任务本身
        # 完全可能是"已暂停"——界面上带「恢复」标签的候选就是，切回一个刚被
        # 暂停的任务是最常见的形态。它不在名单里时，原有时间槽被作废却没有
        # 替代，剩余工时在时间轴上凭空消失，排程还报成功。
        preserved_status_task_ids={context.paused_source_task_id, context.target_task_id},
        additional_dependencies=context.queue_dependencies,
        # 接替任务必须第一个开始：人既然已经决定切过去，就是现在要做它，别的活
        # 一律不许插在它前面。
        first_start_task_id=context.target_task_id,
        preserved_slot_ids={context.target_slot_id},
        setup_exempt_task_pairs={
            frozenset((task_id, predecessor_id))
            for task_id, predecessor_id in context.queue_dependencies
        },
        solver_time_limit=solver_time_limit,
        project_end_date_overrides=project_end_date_overrides,
        # 失败诊断另有专门的一条：暂停切换只会因为"切完某个项目就超出结题日期"
        # 而失败，而通用诊断回答的是"项目排不下"，口径对不上；它还会顺手排一个
        # 后台方案作业，那个作业拿「保存并开始排程」入口去验证，验的不是这次切换。
        include_failure_diagnostics=False,
    )


def solver_horizon_end(
    queue_end: datetime,
    remaining_duration_minutes: dict[int, int],
) -> datetime:
    """Cover the queued workload while keeping the closure itself bounded."""
    workload_days = ceil(sum(remaining_duration_minutes.values()) / (8 * 60))
    return queue_end + timedelta(days=max(2, workload_days + 2))


def _switch_failure(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    started_at: datetime,
    result: dict,
) -> DomainConflictError:
    # 诊断服务反过来要用本模块的 apply_switch_anchors / run_switch_replan，
    # 模块级 import 会成环，只能在这里就地引入。
    from app.services.task_pause_switch_diagnosis_service import (
        diagnose_pause_switch_failure,
    )

    # savepoint 已经回滚，但 _prepare_switch_anchors 改过的那几个属性还留在
    # 会话的身份映射里。诊断要按库里的现状重新算一遍，先让它们失效。
    db.expire_all()
    solver_message = result.get("message")
    _logger.warning(
        "pause_switch_replan_failed source_slot_id=%s target_slot_id=%s message=%s",
        source_slot.id, target_slot.id, solver_message,
    )
    try:
        failure = diagnose_pause_switch_failure(
            db, source_slot, target_slot, started_at, solver_message,
        )
    except Exception:
        # 诊断算不出来时退回求解器给的原话。这一步只是把话说得更清楚，
        # 它自己出问题不该把用户的暂停操作变成 500。
        _logger.exception("暂停切换失败诊断异常 source_slot_id=%s", source_slot.id)
        return DomainConflictError(solver_message or "暂停切换重排失败")
    return DomainConflictError(
        failure["message"],
        detail={
            "message": failure["message"],
            "pause_switch_failure": failure["pause_switch_failure"],
        },
    )


def _prepare_switch_anchors(
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    switch_time: datetime,
) -> None:
    historical_source_start = source_slot.actual_start or switch_time
    if source_slot.actual_end is None:
        source_slot.actual_end = switch_time
    source_slot.task.status = "paused"
    source_slot.plan_start = min(historical_source_start, switch_time)
    source_slot.plan_end = switch_time
    target_slot.plan_start = switch_time
    target_slot.plan_end = switch_time
