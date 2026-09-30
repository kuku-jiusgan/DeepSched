from __future__ import annotations

from datetime import datetime

from app.models import Project, Task, TimeSlot
from app.services.project_plan_apply_helpers import downstream_ids
from app.services.schedule_insert_service import _selected_instrument_ids
from app.services.schedule_slot_protection_service import (
    task_has_immovable_slot,
    tasks_with_immovable_slot,
)


MOVABLE_TIERS = ["confirmed", "forecast"]
MOVABLE_SLOT_STATUSES = ["scheduled", "paused", "blocked", "interrupted"]


def load_later_deadline_movable_tasks(
    db,
    project: Project,
    selected_tasks: list[Task],
    minimum_start: datetime | None = None,
) -> list[Task]:
    """Return future, unstarted tasks from projects with a later deadline."""
    if not project.end_date:
        return []
    selected_ids = {task.id for task in selected_tasks}
    selected_instruments = _selected_instrument_ids(selected_tasks)
    selected_assignees = {task.assignee_id for task in selected_tasks if task.assignee_id}
    if not selected_instruments and not selected_assignees:
        return []
    candidates = db.query(Task).join(Project).filter(
        Task.status.in_(MOVABLE_SLOT_STATUSES),
        ~Task.id.in_(selected_ids),
        Project.id != project.id,
        Project.end_date.isnot(None),
        Project.end_date > project.end_date,
    ).order_by(Project.end_date, Project.priority, Task.created_at, Task.id).all()
    if not candidates:
        return []
    resource_filters = []
    if selected_instruments:
        resource_filters.append(TimeSlot.instrument_id.in_(selected_instruments))
    elif selected_assignees:
        resource_filters.append(Task.assignee_id.in_(selected_assignees))
    candidate_ids = [task.id for task in candidates]
    protected_ids = fully_protected_task_ids(db, set(candidate_ids))
    conflicting_ids = {
        task_id for (task_id,) in db.query(TimeSlot.task_id).join(Task).filter(
            TimeSlot.task_id.in_([
                task_id for task_id in candidate_ids if task_id not in protected_ids
            ]),
            TimeSlot.tier.in_(MOVABLE_TIERS),
            TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
            TimeSlot.plan_end > (minimum_start or datetime.now()),
            *resource_filters,
        ).distinct().all()
    }
    if not conflicting_ids:
        return []

    project_task_ids = {
        task_id for task_id, in db.query(Task.id).join(Project).filter(
            Project.end_date > project.end_date,
        ).all()
    }
    branches = {
        task_id: downstream_ids(db, {task_id}, project_task_ids)
        for task_id in conflicting_ids
    }
    fully_protected = fully_protected_task_ids(
        db, {task_id for branch in branches.values() for task_id in branch},
    )
    affected_ids = set()
    for branch_ids in branches.values():
        if any(task_id in fully_protected for task_id in branch_ids):
            continue
        affected_ids.update(branch_ids)
    if not affected_ids:
        return []
    affected_tasks = db.query(Task).filter(
        Task.id.in_(affected_ids),
        Task.status.in_(MOVABLE_SLOT_STATUSES),
    ).all()
    return [task for task in affected_tasks if task.id not in fully_protected]


def fully_protected_task_ids(db, task_ids: set[int]) -> set[int]:
    if not task_ids:
        return set()
    protected = tasks_with_immovable_slot(db, task_ids)
    if not protected:
        return set()
    still_movable = {
        task_id for (task_id,) in db.query(TimeSlot.task_id).filter(
            TimeSlot.task_id.in_(protected),
            TimeSlot.tier.in_(MOVABLE_TIERS),
            TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_end > datetime.now(),
        ).distinct().all()
    }
    return protected - still_movable


def task_has_protected_slot(db, task_id: int) -> bool:
    return task_has_immovable_slot(db, task_id)


def has_approved_gate_predecessor(task: Task) -> bool:
    """Keep formally approved branches stable during forecast insertion."""
    pending = list(task.predecessors)
    visited: set[int] = set()
    while pending:
        dependency = pending.pop()
        predecessor = dependency.predecessor
        if predecessor.id in visited:
            continue
        visited.add(predecessor.id)
        if predecessor.is_external_gate and predecessor.gate_status == "approved":
            return True
        pending.extend(predecessor.predecessors)
    return False
