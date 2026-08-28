"""方案签批的任务依赖图遍历，以及下游任务的时间槽清理。"""

from __future__ import annotations

from datetime import datetime

from app.models import Task, TaskDependency, TimeSlot
from app.services.approval_gate_errors import ApprovalGateInvalidError
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.task_execution_service import (
    COMPLETED_TASK_STATUSES,
    TaskExecutionInvalidError,
    ensure_predecessors_completed,
)


MOVABLE_SLOT_STATUSES = ["scheduled", "blocked"]


def clear_descendant_slots(db, task_ids: set[int]) -> None:
    if not task_ids:
        return
    delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
        TimeSlot.actual_start.is_(None),
    ), synchronize_session=False)

def downstream_ids(db, seed_ids: set[int]) -> set[int]:
    dependencies = db.query(TaskDependency).all()
    by_predecessor: dict[int, set[int]] = {}
    for dependency in dependencies:
        by_predecessor.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)
    result = set(seed_ids)
    pending = list(seed_ids)
    while pending:
        predecessor_id = pending.pop()
        for task_id in by_predecessor.get(predecessor_id, set()):
            if task_id not in result:
                result.add(task_id)
                pending.append(task_id)
    return result

def descendant_tasks(db, gate_id: int) -> list[Task]:
    task_ids = downstream_ids(db, {gate_id}) - {gate_id}
    return db.query(Task).filter(Task.id.in_(task_ids)).all() if task_ids else []

def upstream_gates(
    task_id: int,
    predecessors: dict[int, set[int]],
    task_by_id: dict[int, Task],
) -> list[Task]:
    result: list[Task] = []
    pending = list(predecessors.get(task_id, set()))
    visited: set[int] = set()
    while pending:
        predecessor_id = pending.pop()
        if predecessor_id in visited:
            continue
        visited.add(predecessor_id)
        predecessor = task_by_id.get(predecessor_id)
        if predecessor and predecessor.is_external_gate:
            result.append(predecessor)
        pending.extend(predecessors.get(predecessor_id, set()))
    return result

def ensure_gate_predecessors_completed(gate: Task) -> None:
    try:
        ensure_predecessors_completed(gate)
    except TaskExecutionInvalidError as exc:
        raise ApprovalGateInvalidError(str(exc))

def task_completed_at(task: Task) -> datetime | None:
    if task.status not in COMPLETED_TASK_STATUSES:
        return None
    actual_ends = [slot.actual_end for slot in task.time_slots if slot.actual_end]
    return max(actual_ends, default=task.updated_at)

def unapproved_gate_context(db, tasks: list[Task]) -> tuple[dict[int, datetime], set[int]]:
    task_ids = {task.id for task in tasks}
    if not task_ids:
        return {}, set()
    project_ids = {task.project_id for task in tasks}
    project_tasks = db.query(Task).filter(Task.project_id.in_(project_ids)).all()
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_({task.id for task in project_tasks})
    ).all()
    predecessors: dict[int, set[int]] = {}
    for dependency in dependencies:
        predecessors.setdefault(dependency.task_id, set()).add(dependency.predecessor_id)
    task_by_id = {task.id: task for task in project_tasks}
    bounds: dict[int, datetime] = {}
    forecast_ids: set[int] = set()
    for task_id in task_ids:
        for gate in upstream_gates(task_id, predecessors, task_by_id):
            bound = gate.approved_at if gate.gate_status == "approved" else gate.expected_approval_at
            if bound and (task_id not in bounds or bound > bounds[task_id]):
                bounds[task_id] = bound
            if gate.gate_status != "approved":
                forecast_ids.add(task_id)
    return bounds, forecast_ids
