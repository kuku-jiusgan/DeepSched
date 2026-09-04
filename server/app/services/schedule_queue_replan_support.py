from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_

from app.core.config import get_settings
from app.models import Task, TimeSlot
from app.services.scheduler_helpers import natural_day_boundary
from app.services.instrument_working_time_service import load_working_time_context
from app.services.schedule_rule_service import get_solver_constraints
from app.services.task_progress_service import remaining_task_minutes
from app.services.schedule_replan_closure_service import collect_replan_task_ids


FORWARD_SHIFT_TASK_STATUSES = ["pending", "scheduled", "blocked", "waiting_external"]


def load_forward_shift_candidates(
    db,
    instrument_id: int | None,
    released_at: datetime,
    assignee_ids: set[int],
    extra_task_ids: set[int] | None = None,
) -> list[Task]:
    """资源空出来后可以前移的任务。

    extra_task_ids 用来放行本来不在待排状态、但这次确实该跟着前移的任务：仪器
    故障暂停的任务在维修完成时就是这种情况——它是暂停态，可它停下来的原因刚好
    是这次被解除的那个，剩余工时必须按真实的维修完成时间前移，而不是留在按预计
    维修时间排出来的位置上。等样品之类的人工暂停不在这个名单里，不受影响。
    """
    resource_filter = _resource_filter(instrument_id, assignee_ids)
    if resource_filter is None:
        return []
    seed_rows = db.query(Task.id).join(TimeSlot, TimeSlot.task_id == Task.id).filter(
        resource_filter,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(("scheduled", "running", "paused", "blocked", "interrupted")),
        TimeSlot.plan_end > released_at,
    ).distinct().all()
    closure_ids = collect_replan_task_ids(
        db,
        {task_id for (task_id,) in seed_rows},
        {instrument_id} if instrument_id is not None else set(),
        assignee_ids,
        released_at,
    )
    if not closure_ids:
        return []
    rows = (
        db.query(Task, TimeSlot.plan_start)
        .join(TimeSlot, TimeSlot.task_id == Task.id)
        .filter(
            Task.id.in_(closure_ids),
            _candidate_status_filter(extra_task_ids),
            TimeSlot.status == "scheduled",
            TimeSlot.plan_end > released_at,
            TimeSlot.actual_start.is_(None),
            TimeSlot.lifecycle_status == "active",
        )
        .filter(resource_filter)
        .all()
    )
    tasks = {}
    first_starts = {}
    for task, plan_start in rows:
        tasks[task.id] = task
        first_starts[task.id] = min(first_starts.get(task.id, plan_start), plan_start)
    return sorted(tasks.values(), key=lambda task: (first_starts[task.id], task.id))


def affected_assignee_ids(db, instrument_id: int | None, assignee_id: int | None, released_at: datetime) -> set[int]:
    assignee_ids = {assignee_id} if assignee_id is not None else set()
    if instrument_id is None:
        return assignee_ids
    rows = db.query(Task.assignee_id).join(TimeSlot, TimeSlot.task_id == Task.id).filter(
        TimeSlot.instrument_id == instrument_id,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
        TimeSlot.plan_end >= released_at,
        Task.assignee_id.isnot(None),
    ).distinct().all()
    assignee_ids.update(value for value, in rows)
    return assignee_ids


def _resource_filter(instrument_id: int | None, assignee_ids: set[int]):
    if instrument_id is not None:
        resource_filter = TimeSlot.instrument_id == instrument_id
        if assignee_ids:
            resource_filter = resource_filter | (
                Task.requires_human.is_(True) & Task.assignee_id.in_(assignee_ids)
            )
        return resource_filter
    if assignee_ids:
        return Task.requires_human.is_(True) & Task.assignee_id.in_(assignee_ids)
    return None


def _candidate_status_filter(extra_task_ids: set[int] | None):
    allowed = Task.status.in_(FORWARD_SHIFT_TASK_STATUSES)
    if not extra_task_ids:
        return allowed
    return or_(allowed, Task.id.in_(extra_task_ids))


def is_movable_task(
    db,
    task: Task,
    instrument_id: int | None,
    released_at: datetime,
    assignee_ids: set[int],
) -> bool:
    """这个任务能不能整体跟着前移。

    判定用的资源范围必须与挑候选时的一模一样。候选是按"槽在这台仪器上，或者
    任务的负责人在受影响的负责人里"挑出来的，而这里一度只认调用方显式传进来的
    那一个负责人：仪器故障提前修好时根本没传，于是被负责人牵连进来的纯人工任务
    （方案撰写这类不占仪器的活）永远判不movable。队列又是"遇到第一个不能动的
    就停"，队首恰好是这种任务时，整条队列一个都不前移——现象就是故障顺延了后面
    的任务，提前修好却没有任何任务回来。
    """
    frozen = db.query(TimeSlot.id).filter(
        TimeSlot.task_id == task.id,
        TimeSlot.status == "scheduled",
        TimeSlot.tier == "frozen",
        TimeSlot.lifecycle_status == "active",
    ).first()
    if frozen:
        return False
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id == task.id,
        TimeSlot.status == "scheduled",
        TimeSlot.plan_end > released_at,
        TimeSlot.actual_start.is_(None),
        TimeSlot.lifecycle_status == "active",
    ).all()
    return bool(slots) and all(
        (instrument_id is not None and slot.instrument_id == instrument_id)
        or _belongs_to_affected_assignee(task, assignee_ids)
        for slot in slots
    )


def _belongs_to_affected_assignee(task: Task, assignee_ids: set[int]) -> bool:
    """与 _resource_filter 的负责人分支保持逐字一致，两边不能各判各的。"""
    return bool(
        task.requires_human
        and task.assignee_id is not None
        and task.assignee_id in assignee_ids
    )


def load_working_options(db, released_at: datetime) -> dict:
    context = load_working_time_context(db, released_at)
    policy = context.global_policy
    return {
        "day_start_minutes": policy.day_start_minutes,
        "day_end_minutes": policy.day_end_minutes,
        "include_weekends": policy.include_weekends,
        "include_holidays": policy.include_holidays,
        "horizon_end": context.horizon_end,
        "calendar_days": context.calendar_days,
        "working_time_context": context,
    }


def cross_project_setup_minutes(db) -> int:
    rule = get_solver_constraints(db)["cross_project_setup"]
    if not rule.is_enabled:
        return 0
    return max(0, round(float((rule.params or {}).get("setup_hours", 0.5)) * 60))


def dependency_ready_time(db, task: Task, fallback: datetime) -> datetime:
    ready_at = fallback
    for dependency in task.predecessors:
        predecessor_end = _predecessor_ready_time(db, dependency.predecessor_id)
        if predecessor_end and predecessor_end > ready_at:
            ready_at = predecessor_end
    return ready_at


def _predecessor_ready_time(db, task_id: int) -> datetime | None:
    predecessor = db.query(Task).filter(Task.id == task_id).first()
    if predecessor and predecessor.status in {"done", "completed"}:
        actual_end = max((
            value for value, in db.query(TimeSlot.actual_end).filter(
            TimeSlot.task_id == task_id,
            TimeSlot.lifecycle_status == "active",
            TimeSlot.actual_end.isnot(None),
        ).all()
        ), default=None)
        if actual_end:
            return actual_end
    return max((
        value for value, in db.query(TimeSlot.plan_end).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.lifecycle_status == "active",
    ).all()
    ), default=None)


def replan_duration_minutes(task: Task, slots: list[TimeSlot]) -> int:
    if task.est_duration_hours is not None:
        return remaining_task_minutes(task)
    return sum(
        int((slot.plan_end - slot.plan_start).total_seconds() / 60)
        for slot in slots
    )


def tier_for_start(db, start: datetime) -> str:
    settings = get_settings()
    rule = get_solver_constraints(db)["freezing"]
    freeze_days = int((rule.params or {}).get("freeze_days", settings.FROZEN_DAYS))
    now = datetime.now()
    if start <= natural_day_boundary(now, freeze_days):
        return "frozen"
    if start <= now + timedelta(days=settings.CONFIRMED_DAYS):
        return "confirmed"
    return "forecast"
