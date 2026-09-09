from __future__ import annotations

from datetime import datetime

from app.models import Project, Task, TaskDependency


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
    return sorted(dependencies)


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
