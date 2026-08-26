from __future__ import annotations

from app.models import Task, TaskDependency


CONTINUOUS_SUCCESSOR_TYPES = {
    ("FFKF_001", "QCFA_001"),
    ("FFYZ_001", "ZXBG_001"),
}


def is_valid_continuous_successor(predecessor: Task, successor: Task) -> bool:
    return (
        predecessor.project_id == successor.project_id
        and predecessor.parent_id is not None
        and predecessor.parent_id == successor.parent_id
        and (predecessor.task_type, successor.task_type) in CONTINUOUS_SUCCESSOR_TYPES
    )


def create_continuous_successor(predecessor: Task, successor: Task) -> TaskDependency:
    if not is_valid_continuous_successor(predecessor, successor):
        raise ValueError("连续后续任务必须属于同一项目、同一顶级任务分组且任务类型匹配")
    return TaskDependency(
        task_id=successor.id,
        predecessor_id=predecessor.id,
        dependency_type="continuous_successor",
    )
