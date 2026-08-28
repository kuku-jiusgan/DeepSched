from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models import Project, Task, TaskDependency, TimeSlot
from app.services.project_plan_template_service import POST_APPROVAL_STEPS
from app.services.task_progress_service import remaining_task_minutes


FINISHED_TASK_STATUSES = {"done", "completed"}
POST_APPROVAL_RATIO = sum(float(percentage) for _, _, percentage, _ in POST_APPROVAL_STEPS)


@dataclass(frozen=True)
class PendingWorkload:
    """项目中"签批通过后才会进入排程"的剩余工时。

    方案签批未通过时，方法验证与报告撰写不占用任何时间槽，因此它们的工时
    对任何以 TimeSlot 为唯一依据的推算都是不可见的。本结构把这段工时显式
    地算出来，供交付预测和插单影响评估使用。
    """

    hours: float = 0.0
    gate_expected_at: datetime | None = None
    # tasks: 下游任务已存在但未排程；template: 下游任务尚未创建，按模板比例推算；
    # none: 没有未签批的签批节点。
    source: str = "none"

    @property
    def has_expected_approval(self) -> bool:
        return self.gate_expected_at is not None


def pending_approval_workload(db, project_ids: set[int]) -> dict[int, PendingWorkload]:
    """批量计算各项目的签批后未排工时。

    对任务状态不敏感：同一批任务在不同路径下可能是 waiting_external、pending，
    或者根本还没被创建，判定依据统一为"是否已有活跃时间槽"。
    """
    project_ids = {int(project_id) for project_id in project_ids or set()}
    if not project_ids:
        return {}

    tasks = db.query(Task).filter(Task.project_id.in_(project_ids)).all()
    if not tasks:
        return {project_id: PendingWorkload() for project_id in project_ids}

    task_by_id = {task.id: task for task in tasks}
    successors = _successor_map(db, set(task_by_id))
    parent_ids = {task.parent_id for task in tasks if task.parent_id is not None}
    scheduled_ids = _scheduled_task_ids(db, set(task_by_id))
    estimated_hours = _project_estimated_hours(db, project_ids)

    tasks_by_project: dict[int, list[Task]] = {project_id: [] for project_id in project_ids}
    for task in tasks:
        tasks_by_project.setdefault(task.project_id, []).append(task)

    return {
        project_id: _project_workload(
            tasks_by_project.get(project_id, []),
            task_by_id,
            successors,
            parent_ids,
            scheduled_ids,
            estimated_hours.get(project_id, 0.0),
        )
        for project_id in project_ids
    }


def _project_workload(
    project_tasks: list[Task],
    task_by_id: dict[int, Task],
    successors: dict[int, set[int]],
    parent_ids: set[int],
    scheduled_ids: set[int],
    estimated_hours: float,
) -> PendingWorkload:
    gates = [
        task for task in project_tasks
        if task.is_external_gate and task.gate_status != "approved"
    ]
    if not gates:
        return PendingWorkload()

    expected_at = max(
        (gate.expected_approval_at for gate in gates if gate.expected_approval_at),
        default=None,
    )
    downstream = _downstream_tasks(gates, task_by_id, successors, parent_ids)
    if not downstream:
        return PendingWorkload(
            hours=round(max(0.0, estimated_hours) * POST_APPROVAL_RATIO, 2),
            gate_expected_at=expected_at,
            source="template",
        )

    minutes = sum(
        remaining_task_minutes(task)
        for task in downstream
        if task.id not in scheduled_ids
    )
    return PendingWorkload(
        hours=round(minutes / 60, 2),
        gate_expected_at=expected_at,
        source="tasks",
    )


def _downstream_tasks(
    gates: list[Task],
    task_by_id: dict[int, Task],
    successors: dict[int, set[int]],
    parent_ids: set[int],
) -> list[Task]:
    visited: set[int] = set()
    pending = [task_id for gate in gates for task_id in successors.get(gate.id, set())]
    result: list[Task] = []
    while pending:
        task_id = pending.pop()
        if task_id in visited:
            continue
        visited.add(task_id)
        pending.extend(successors.get(task_id, set()))
        task = task_by_id.get(task_id)
        if task is None or task.is_external_gate or task.id in parent_ids:
            continue
        if task.status in FINISHED_TASK_STATUSES:
            continue
        result.append(task)
    return result


def _successor_map(db, task_ids: set[int]) -> dict[int, set[int]]:
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.predecessor_id.in_(task_ids),
    ).all()
    successors: dict[int, set[int]] = {}
    for dependency in dependencies:
        successors.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)
    return successors


def _scheduled_task_ids(db, task_ids: set[int]) -> set[int]:
    rows = db.query(TimeSlot.task_id).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
    ).distinct().all()
    return {row[0] for row in rows}


def _project_estimated_hours(db, project_ids: set[int]) -> dict[int, float]:
    rows = db.query(Project.id, Project.estimated_hours).filter(
        Project.id.in_(project_ids),
    ).all()
    return {row[0]: float(row[1] or 0) for row in rows}
