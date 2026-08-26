from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models import Task, TaskDependency, TimeSlot


@dataclass(frozen=True)
class ApprovalScheduleContext:
    gate_id: int
    downstream_task_ids: set[int]
    branch_task_ids: set[int]
    anchor_at: datetime | None


def approval_top_level_task_name(db, gate: Task) -> str | None:
    project_tasks = db.query(Task).filter(Task.project_id == gate.project_id).all()
    task_by_id = {task.id: task for task in project_tasks}
    root_id = _top_parent(gate, task_by_id) if gate.parent_id else None
    if root_id is None:
        dependencies = db.query(TaskDependency).filter(
            TaskDependency.task_id == gate.id,
        ).all()
        for predecessor_id in sorted(_direct_predecessor_ids(gate.id, dependencies)):
            predecessor = task_by_id.get(predecessor_id)
            root_id = _top_parent(predecessor, task_by_id)
            if root_id is not None:
                break
    root = task_by_id.get(root_id) if root_id is not None else None
    return root.name if root and root.id != gate.id else None


def build_approval_schedule_context(db, gate: Task) -> ApprovalScheduleContext:
    project_tasks = db.query(Task).filter(Task.project_id == gate.project_id).all()
    task_by_id = {task.id: task for task in project_tasks}
    project_task_ids = set(task_by_id)
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(project_task_ids),
    ).all()
    downstream_ids = _downstream_ids(dependencies, {gate.id}) - {gate.id}
    branch_ids = _branch_task_ids(gate, task_by_id, dependencies)
    branch_anchor_at = _branch_anchor_at(db, branch_ids - downstream_ids)
    if gate.gate_status == "approved":
        # 正式签批立即进入排程：接在相关仪器当前运行任务之后；若没有
        # 运行任务，则从当前时间开始，不等待原先填写的预计签批时间。
        anchor_at = _active_resource_anchor(db, downstream_ids)
    else:
        # 预计签批只用于预测，后续任务不能早于预计签批时间。
        approval_at = gate.expected_approval_at
        anchor_at = max(
            (value for value in (branch_anchor_at, approval_at) if value is not None),
            default=None,
        )
    return ApprovalScheduleContext(
        gate_id=gate.id,
        downstream_task_ids=downstream_ids,
        branch_task_ids=branch_ids,
        anchor_at=anchor_at,
    )


def _branch_task_ids(
    gate: Task,
    task_by_id: dict[int, Task],
    dependencies: list[TaskDependency],
) -> set[int]:
    root = _top_parent(gate, task_by_id) if gate.parent_id else None
    if root is None:
        for predecessor_id in _direct_predecessor_ids(gate.id, dependencies):
            predecessor = task_by_id.get(predecessor_id)
            root = (
                _top_parent(predecessor, task_by_id)
                if predecessor and predecessor.parent_id else None
            )
            if root is not None:
                break
    if root is not None:
        return {
            task.id for task in task_by_id.values()
            if _top_parent(task, task_by_id) == root
        }
    seed_ids = _direct_predecessor_ids(gate.id, dependencies) or {gate.id}
    return _downstream_ids(dependencies, seed_ids) | seed_ids


def _top_parent(task: Task | None, task_by_id: dict[int, Task]) -> int | None:
    if task is None:
        return None
    current = task
    seen: set[int] = set()
    while current.parent_id and current.parent_id not in seen:
        seen.add(current.id)
        parent = task_by_id.get(current.parent_id)
        if parent is None:
            break
        current = parent
    return current.id if current.parent_id is None else None


def _direct_predecessor_ids(
    task_id: int,
    dependencies: list[TaskDependency],
) -> set[int]:
    return {
        dependency.predecessor_id
        for dependency in dependencies
        if dependency.task_id == task_id
    }


def _downstream_ids(
    dependencies: list[TaskDependency],
    seed_ids: set[int],
) -> set[int]:
    downstream_by_predecessor: dict[int, set[int]] = {}
    for dependency in dependencies:
        downstream_by_predecessor.setdefault(
            dependency.predecessor_id,
            set(),
        ).add(dependency.task_id)
    result = set(seed_ids)
    pending = list(seed_ids)
    while pending:
        predecessor_id = pending.pop()
        for task_id in downstream_by_predecessor.get(predecessor_id, set()):
            if task_id not in result:
                result.add(task_id)
                pending.append(task_id)
    return result


def _branch_anchor_at(db, task_ids: set[int]) -> datetime | None:
    if not task_ids:
        return None
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.status.in_([
            "scheduled", "running", "paused", "blocked", "interrupted", "completed",
        ]),
    ).all()
    slot_ends = [slot.actual_end or slot.plan_end for slot in slots]
    if slot_ends:
        return max(slot_ends)
    completed_tasks = db.query(Task).filter(
        Task.id.in_(task_ids),
        Task.status.in_(["done", "completed"]),
    ).all()
    fallback_ends = [task.updated_at for task in completed_tasks if task.updated_at]
    return max(fallback_ends, default=None)


def _active_resource_anchor(db, task_ids: set[int]) -> datetime:
    now = datetime.now()
    if not task_ids:
        return now
    instrument_ids = {
        instrument_id
        for instrument_id, in db.query(TimeSlot.instrument_id).filter(
            TimeSlot.task_id.in_(task_ids),
            TimeSlot.instrument_id.isnot(None),
            TimeSlot.lifecycle_status == "active",
        ).distinct().all()
    }
    instrument_ids.update(
        int(instrument_id)
        for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
        for instrument_id in (task.instrument_ids or [])
    )
    if not instrument_ids:
        return now
    running_ends = [
        slot.plan_end
        for slot in db.query(TimeSlot).filter(
            TimeSlot.instrument_id.in_(instrument_ids),
            ~TimeSlot.task_id.in_(task_ids),
            TimeSlot.status == "running",
            TimeSlot.actual_start.isnot(None),
            TimeSlot.actual_end.is_(None),
            TimeSlot.lifecycle_status == "active",
        ).all()
        if slot.plan_end
    ]
    return max([now, *running_ends])
