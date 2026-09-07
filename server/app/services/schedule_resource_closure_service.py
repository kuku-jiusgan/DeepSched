from __future__ import annotations

from datetime import datetime

from app.models import Task, TimeSlot
from app.services.schedule_insert_service import (
    _load_lower_priority_movable_tasks,
    _selected_instrument_ids,
)


def load_resource_closure_movable_tasks(
    db,
    insert_priority: int,
    selected_tasks: list[Task],
    *,
    include_same_priority: bool,
    minimum_start: datetime | None = None,
    same_priority_after: datetime | None = None,
) -> list[Task]:
    """Expand an insert's movable set through newly affected resources."""
    selected_ids = {task.id for task in selected_tasks}
    discovered: dict[int, Task] = {}
    frontier = list(selected_tasks)
    while frontier:
        candidates = _load_resource_candidates(
            db,
            insert_priority,
            selected_ids | set(discovered),
            _unique_tasks(frontier),
            include_same_priority,
            minimum_start,
            same_priority_after,
        )
        frontier = []
        for task in candidates:
            if task.id in selected_ids or task.id in discovered:
                continue
            discovered[task.id] = task
            frontier.append(task)
    return list(discovered.values())


def _load_resource_candidates(
    db,
    insert_priority: int,
    excluded_task_ids: set[int],
    resource_tasks: list[Task],
    include_same_priority: bool,
    minimum_start: datetime | None,
    same_priority_after: datetime | None,
) -> list[Task]:
    assignee_ids = {
        task.assignee_id
        for task in resource_tasks
        if task.requires_human and task.assignee_id
    }
    return _load_lower_priority_movable_tasks(
        db,
        insert_priority,
        excluded_task_ids,
        _selected_instrument_ids(resource_tasks),
        assignee_ids,
        include_same_priority=include_same_priority,
        minimum_start=minimum_start,
        same_priority_after=same_priority_after,
    )


def active_slot_starts(db, tasks: list[Task]) -> dict[int, datetime]:
    task_ids = {task.id for task in tasks}
    if not task_ids:
        return {}
    rows = db.query(TimeSlot.task_id, TimeSlot.plan_start).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.tier.in_(["confirmed", "forecast"]),
        TimeSlot.status.in_(
            ["scheduled", "paused", "blocked", "interrupted"],
        ),
        TimeSlot.plan_start.isnot(None),
    ).all()
    starts: dict[int, datetime] = {}
    for task_id, start in rows:
        starts[task_id] = min(start, starts.get(task_id, start))
    return starts


def earliest_active_slot_start(db, tasks: list[Task]) -> datetime | None:
    return min(active_slot_starts(db, tasks).values(), default=None)


def _unique_tasks(tasks: list[Task]) -> list[Task]:
    return list({task.id: task for task in tasks}.values())
