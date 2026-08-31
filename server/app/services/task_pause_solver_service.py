from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil

from app.domain.errors import DomainConflictError
from app.models import TimeSlot
from app.services.resource_replan_service import replan_resource_closure
from app.services.schedule_slot_change_log_service import supersede_slot
from app.services.task_pause_switch_context_service import build_pause_switch_context


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
        _prepare_switch_anchors(source_slot, target_slot, context.switch_time)
        for slot in context.replaceable_slots:
            supersede_slot(db, slot, "暂停切换重排")
        db.flush()

        result = replan_resource_closure(
            db,
            context.task_ids,
            context.switch_time,
            source_slot.task.project_id,
            earliest_start_bounds={task_id: context.switch_time for task_id in context.task_ids},
            advance_notification_reason="暂停切换重排",
            remaining_duration_minutes=context.remaining_duration_minutes,
            planning_start_at=context.switch_time,
            planning_end_at=_solver_horizon_end(
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
            preserved_status_task_ids={context.paused_source_task_id, target_slot.task_id},
            additional_dependencies=context.queue_dependencies,
            preserved_slot_ids={target_slot.id},
            setup_exempt_task_pairs={
                frozenset((task_id, predecessor_id))
                for task_id, predecessor_id in context.queue_dependencies
            },
            solver_time_limit=8.0,
        )
        if result.get("status") != "ok":
            savepoint.rollback()
            message = result.get("message") or "暂停切换重排失败"
            detail = {"message": message, "schedule_failure": result.get("schedule_failure")}
            raise DomainConflictError(message, detail=detail if detail["schedule_failure"] else None)
        savepoint.commit()
    except Exception:
        if savepoint.is_active:
            savepoint.rollback()
        raise
    return context.switch_time


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


def _solver_horizon_end(
    queue_end: datetime,
    remaining_duration_minutes: dict[int, int],
) -> datetime:
    """Cover the queued workload while keeping the closure itself bounded."""
    workload_days = ceil(sum(remaining_duration_minutes.values()) / (8 * 60))
    return queue_end + timedelta(days=max(2, workload_days + 2))
