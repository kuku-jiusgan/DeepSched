from __future__ import annotations

from datetime import datetime

from app.models import Task, TaskDependency, TimeSlot
from app.services.task_dependency_service import is_valid_continuous_successor


def target_followup_groups(
    db,
    target_task: Task,
    switch_time: datetime,
    candidate_slot_statuses: set[str],
) -> list[list[TimeSlot]]:
    """Return unstarted continuous successors from the target's own parent group."""
    tasks = db.query(Task).filter(Task.project_id == target_task.project_id).all()
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_([task.id for task in tasks]),
        TaskDependency.dependency_type == "continuous_successor",
    ).all()
    dependencies = [
        dependency for dependency in dependencies
        if is_valid_continuous_successor(dependency.predecessor, dependency.task)
    ]
    descendants = _descendant_ids(target_task.id, dependencies)
    if not descendants:
        return []

    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(descendants),
        TimeSlot.status.in_(candidate_slot_statuses),
        TimeSlot.actual_start.is_(None),
        TimeSlot.plan_start >= switch_time,
        TimeSlot.lifecycle_status == "active",
    ).order_by(TimeSlot.plan_start, TimeSlot.id).all()
    grouped: dict[int, list[TimeSlot]] = {}
    for slot in slots:
        grouped.setdefault(slot.task_id, []).append(slot)
    task_order = {task.id: (task.plan_order, task.id) for task in tasks}
    return [
        grouped[task_id]
        for task_id in sorted(grouped, key=lambda item: task_order.get(item, (0, item)))
    ]


def _descendant_ids(target_task_id: int, dependencies: list[TaskDependency]) -> set[int]:
    descendants: set[int] = set()
    frontier = {target_task_id}
    while frontier:
        child_ids = {
            dependency.task_id
            for dependency in dependencies
            if dependency.predecessor_id in frontier
        } - descendants - {target_task_id}
        if not child_ids:
            break
        descendants.update(child_ids)
        frontier = child_ids
    return descendants
