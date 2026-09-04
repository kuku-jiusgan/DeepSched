from __future__ import annotations

from datetime import datetime

from app.models import Task, TaskDependency, TimeSlot


_ACTIVE_SLOT_STATUSES = ("scheduled", "running", "paused", "blocked", "interrupted")


# 暂停和中断的任务也要进重排闭包：它们只是现在没在做，位置照样该跟着让路。
# 把它们挡在闭包外面，时间槽就钉死在原地，别的任务只能绕着排。
MOVABLE_TASK_STATUSES = ("pending", "scheduled", "blocked", "waiting_external", "paused", "interrupted")


def collect_replan_task_ids(
    db,
    seed_task_ids: set[int],
    instrument_ids: set[int],
    assignee_ids: set[int],
    released_at: datetime,
) -> set[int]:
    """Build the transitive resource/dependency closure for a local replan."""
    task_ids = set(seed_task_ids)
    instruments = set(instrument_ids)
    assignees = set(assignee_ids)
    for _ in range(20):
        before = (len(task_ids), len(instruments), len(assignees))
        resource_ids = _resource_task_ids(db, instruments, assignees, released_at)
        task_ids.update(resource_ids)
        dependency_rows = db.query(TaskDependency.task_id, TaskDependency.predecessor_id).filter(
            (TaskDependency.task_id.in_(task_ids))
            | (TaskDependency.predecessor_id.in_(task_ids))
        ).all() if task_ids else []
        dependency_task_ids = {
            task_id
            for pair in dependency_rows
            for task_id in pair
        }
        movable_dependency_ids = {
            task_id
            for task_id, in db.query(Task.id).filter(
                Task.id.in_(dependency_task_ids),
                Task.status.in_(MOVABLE_TASK_STATUSES),
            ).all()
        }
        task_ids.update(movable_dependency_ids)
        if task_ids:
            rows = db.query(Task.id, Task.assignee_id).filter(Task.id.in_(task_ids)).all()
            assignees.update(assignee_id for _, assignee_id in rows if assignee_id is not None)
            slot_rows = db.query(TimeSlot.instrument_id).filter(
                TimeSlot.task_id.in_(task_ids),
                TimeSlot.lifecycle_status == "active",
                TimeSlot.plan_end > released_at,
                TimeSlot.instrument_id.isnot(None),
            ).distinct().all()
            instruments.update(instrument_id for (instrument_id,) in slot_rows)
        after = (len(task_ids), len(instruments), len(assignees))
        if after == before:
            break
    return task_ids


def _resource_task_ids(db, instrument_ids: set[int], assignee_ids: set[int], released_at: datetime) -> set[int]:
    if not instrument_ids and not assignee_ids:
        return set()
    resource_filter = None
    if instrument_ids:
        resource_filter = TimeSlot.instrument_id.in_(instrument_ids)
    if assignee_ids:
        human_filter = Task.requires_human.is_(True) & Task.assignee_id.in_(assignee_ids)
        resource_filter = human_filter if resource_filter is None else resource_filter | human_filter
    rows = db.query(Task.id).join(TimeSlot, TimeSlot.task_id == Task.id).filter(
        resource_filter,
        Task.status.in_(MOVABLE_TASK_STATUSES),
        TimeSlot.status.in_(_ACTIVE_SLOT_STATUSES),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.plan_end > released_at,
    ).distinct().all()
    return {task_id for (task_id,) in rows}
