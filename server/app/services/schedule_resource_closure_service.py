from __future__ import annotations

from datetime import datetime

from app.models import Task
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
    )


def _unique_tasks(tasks: list[Task]) -> list[Task]:
    return list({task.id: task for task in tasks}.values())
