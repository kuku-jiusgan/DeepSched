"""Dependencies that keep approval-inserted work from splitting task chains."""

from __future__ import annotations

import logging

from app.models import Task, TaskDependency
from app.services.project_plan_errors import ProjectPlanInvalidError
from app.services.task_dependency_service import is_valid_continuous_successor


_logger = logging.getLogger(__name__)


def build_approval_insert_dependencies(
    db,
    selected_tasks: list[Task],
    movable_tasks: list[Task],
) -> list[tuple[int, int]]:
    """Keep continuous successor pairs together during approval insertion.

    Capacity constraints alone allow newly approved work to fit between a
    method-development task and its paired scheme-writing task. The dependency
    is recorded when the project plan is created, so approval replanning must
    carry an ordering edge for competing resource work.
    """
    movable_by_id = {task.id: task for task in movable_tasks}
    if not selected_tasks or not movable_tasks:
        return []
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(movable_by_id),
        TaskDependency.dependency_type == "continuous_successor",
    ).all()
    pairs: set[tuple[int, int]] = set()
    # The approved project's downstream tasks are the insertion being served.
    # They must take the queue position before work that is moved out of the
    # way, even when the moved task is not part of a continuous method/report
    # pair.  Capacity ``NoOverlap`` only forbids intersection; it does not
    # choose which project goes first.  Without this edge CP-SAT can keep an
    # old movable slot at the same start and leave the conflict to the
    # post-persist validator.
    for selected in selected_tasks:
        for movable in movable_tasks:
            if movable.project_id == selected.project_id:
                continue
            if (
                not selected.requires_instrument
                and movable.requires_instrument
                and _tasks_share_resource(selected, movable)
            ):
                pairs.add((movable.id, selected.id))

    for dependency in dependencies:
        if not is_valid_continuous_successor(dependency.predecessor, dependency.task):
            raise ProjectPlanInvalidError(
                f"任务【{dependency.task.name}】的连续后续关系与项目计划不一致，请检查任务类型和分组"
            )
        predecessor_id = dependency.predecessor_id
        successor_id = dependency.task_id
        for selected in selected_tasks:
            if selected.project_id == dependency.task.project_id:
                continue
            if _shares_task_resource(selected, dependency.predecessor, dependency.task):
                if predecessor_id in movable_by_id:
                    # 尚未开始的连续任务整体让位；已开始或固定的前驱先完成其后续。
                    pairs.add((predecessor_id, selected.id))
                else:
                    pairs.add((selected.id, successor_id))
    if pairs:
        _logger.info("approval_insert_continuous_dependencies edges=%s", sorted(pairs))
    return sorted(pairs)


def _shares_task_resource(candidate: Task, first: Task, second: Task) -> bool:
    return any(
        _tasks_share_resource(candidate, task)
        for task in (first, second)
    )


def _tasks_share_resource(first: Task, second: Task) -> bool:
    if (first.requires_instrument and second.requires_instrument
            and set(first.instrument_ids or []) & set(second.instrument_ids or [])):
        return True
    return bool(
        first.requires_human
        and second.requires_human
        and first.assignee_id
        and first.assignee_id == second.assignee_id
    )
