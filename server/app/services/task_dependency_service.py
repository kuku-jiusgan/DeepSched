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


def resolve_dependency_type(predecessor: Task | None, successor: Task | None) -> str:
    """一条前置关系该记成什么类型。

    判定只有这一处，模板导入、手工建任务、修改前置都调它，免得同一件事出现几套
    规则：曾经手工连的一律记成普通前置，要等下次重启才被启动脚本按另一套更松的
    规则（不看父分组、允许交叉配对）改成连续后续，同一个操作的结果取决于有没有
    重启过，改出来的关系消费方还不认。
    """
    if predecessor is None or successor is None:
        return "predecessor"
    return (
        "continuous_successor"
        if is_valid_continuous_successor(predecessor, successor)
        else "predecessor"
    )


def create_continuous_successor(predecessor: Task, successor: Task) -> TaskDependency:
    if not is_valid_continuous_successor(predecessor, successor):
        raise ValueError("连续后续任务必须属于同一项目、同一顶级任务分组且任务类型匹配")
    return TaskDependency(
        task_id=successor.id,
        predecessor_id=predecessor.id,
        dependency_type="continuous_successor",
    )
