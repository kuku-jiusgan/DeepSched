from __future__ import annotations

from datetime import datetime

from app.models import Project, Task, TaskDependency
from app.services.scheduler_data import load_task_children
from app.services.scheduler_helpers import build_dependencies


def build_schedule_priority_dependencies(
    db,
    project: Project,
    selected_tasks: list[Task],
    movable_tasks: list[Task],
) -> list[tuple[int, int]]:
    replan_tasks = _unique_tasks([*selected_tasks, *movable_tasks])
    dependencies = _inserted_detection_dependencies(
        db, project, selected_tasks, movable_tasks,
    )
    dependencies.update(_fixed_detection_dependencies(db, replan_tasks))
    precedence = set(build_dependencies(
        replan_tasks, load_task_children(db, replan_tasks),
    )) | dependencies
    dependencies.update(_manual_queue_dependencies(
        db, selected_tasks, movable_tasks, precedence,
    ))
    return sorted(dependencies)


def _manual_queue_dependencies(
    db,
    selected_tasks: list[Task],
    movable_tasks: list[Task],
    precedence: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Order same-assignee manual work without overriding project priority.

    Manual work has no instrument alternative, but it still occupies its
    assignee. Instrument-only queue rules cannot order this pair, so an
    explicit dependency keeps the queue deterministic. Higher-priority
    selected work goes first; equal- or lower-priority selected work keeps the
    existing manual task first.
    """
    replan_ids = {task.id for task in [*selected_tasks, *movable_tasks]}
    manual_ids = {
        task.id for task in movable_tasks
        if task.requires_human and not task.requires_instrument
    }
    blocked_manual_ids = _tasks_with_unfinished_predecessors(
        db, manual_ids, allowed_predecessor_ids=replan_ids,
    )
    dependencies = set()
    for selected in selected_tasks:
        for movable in movable_tasks:
            if (
                movable.id in blocked_manual_ids
                or not _shares_manual_assignee(selected, movable)
            ):
                continue
            if _project_priority(selected) < _project_priority(movable):
                dependency = (movable.id, selected.id)
            else:
                dependency = (selected.id, movable.id)
            if not _already_ordered(*dependency, precedence):
                dependencies.add(dependency)
    return dependencies


def _shares_manual_assignee(selected: Task, movable: Task) -> bool:
    return (
        selected.project_id != movable.project_id
        and selected.requires_instrument
        and movable.requires_human
        and not movable.requires_instrument
        and selected.assignee_id is not None
        and selected.assignee_id == movable.assignee_id
    )


def _project_priority(task: Task) -> int:
    priority = task.project.priority if task.project else None
    return int(priority if priority is not None else 3)


def _already_ordered(
    successor_id: int,
    predecessor_id: int,
    precedence: set[tuple[int, int]],
) -> bool:
    """Keep an existing order and avoid adding its inverse as a cycle."""
    return (
        _depends_on(successor_id, predecessor_id, precedence)
        or _depends_on(predecessor_id, successor_id, precedence)
    )


def _depends_on(
    task_id: int, predecessor_id: int, precedence: set[tuple[int, int]],
) -> bool:
    predecessors: dict[int, set[int]] = {}
    for successor, predecessor in precedence:
        predecessors.setdefault(successor, set()).add(predecessor)
    pending = [task_id]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current == predecessor_id:
            return True
        if current not in visited:
            visited.add(current)
            pending.extend(predecessors.get(current, set()))
    return False


def _inserted_detection_dependencies(
    db,
    project: Project,
    selected_tasks: list[Task],
    movable_tasks: list[Task],
) -> set[tuple[int, int]]:
    if project.project_kind != "detection":
        return set()
    blocked_movable_ids = _tasks_with_unfinished_predecessors(
        db, {task.id for task in movable_tasks},
    )
    return {
        (movable.id, selected.id)
        for movable in movable_tasks
        for selected in selected_tasks
        if movable.id not in blocked_movable_ids
        if int(movable.project.priority or 3) > int(project.priority or 3)
        and _shares_resource(movable, selected)
    }


def _tasks_with_unfinished_predecessors(
    db,
    task_ids: set[int],
    allowed_predecessor_ids: set[int] | None = None,
) -> set[int]:
    if not task_ids:
        return set()
    status_by_id = {task.id: task.status for task in db.query(Task).all()}
    predecessors: dict[int, set[int]] = {}
    for dependency in db.query(TaskDependency).all():
        predecessors.setdefault(dependency.task_id, set()).add(
            dependency.predecessor_id,
        )
    blocked: set[int] = set()
    allowed_predecessor_ids = allowed_predecessor_ids or set()
    for task_id in task_ids:
        pending = list(predecessors.get(task_id, set()))
        visited: set[int] = set()
        while pending:
            predecessor_id = pending.pop()
            if predecessor_id in visited:
                continue
            visited.add(predecessor_id)
            if (
                predecessor_id not in allowed_predecessor_ids
                and status_by_id.get(predecessor_id) not in {"done", "completed"}
            ):
                blocked.add(task_id)
                break
            pending.extend(predecessors.get(predecessor_id, set()))
    return blocked


def _fixed_detection_dependencies(db, replan_tasks: list[Task]) -> set[tuple[int, int]]:
    replan_ids = {task.id for task in replan_tasks}
    detections = db.query(Task).join(Project).filter(
        Project.project_kind == "detection",
        Task.status == "scheduled",
    ).all()
    return {
        (task.id, detection.id)
        for task in replan_tasks
        for detection in detections
        if detection.id not in replan_ids
        and task.project_id != detection.project_id
        and int(task.project.priority or 3) > int(detection.project.priority or 3)
        and _has_future_unstarted_slot(detection)
        and _shares_resource(task, detection)
    }


def _has_future_unstarted_slot(task: Task) -> bool:
    now = datetime.now()
    return any(
        slot.lifecycle_status == "active"
        and slot.actual_start is None
        and slot.status in {"scheduled", "blocked"}
        and slot.plan_end > now
        for slot in task.time_slots
    )


def _shares_resource(first: Task, second: Task) -> bool:
    shares_instrument = bool(set(first.instrument_ids or []) & set(second.instrument_ids or []))
    shares_assignee = bool(
        first.requires_human
        and second.requires_human
        and first.assignee_id
        and first.assignee_id == second.assignee_id
    )
    return shares_instrument or shares_assignee


def _unique_tasks(tasks: list[Task]) -> list[Task]:
    return list({task.id: task for task in tasks}.values())
