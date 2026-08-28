from __future__ import annotations

from sqlalchemy import and_, not_, or_, select
from sqlalchemy.orm import selectinload

from app.models import Instrument, Project, Task


def load_scheduler_data(
    db,
    project_ids=None,
    task_ids=None,
    excluded_task_ids: set[int] | None = None,
    replan_task_ids: set[int] | None = None,
    occupancy_project_ids: set[int] | None = None,
):
    child_parent_ids = select(Task.parent_id).where(Task.parent_id.isnot(None))
    # 等待方案签批的下游任务也必须进入预测排程，否则签批完成后才会
    # 首次占用资源，导致已排任务被突然挤压。外部签批节点本身仍排除，
    # 它不占用仪器/人员资源；下游任务会由 approval gate context 施加
    # 预计签批时间边界，并在持久化时标记为 forecast。
    schedulable_status = or_(
        Task.status.in_(["pending", "ready"]),
        and_(
            Task.status == "waiting_external",
            Task.predecessors.any(),
        ),
    )
    replan_task_ids = replan_task_ids or set()
    query = db.query(Task).filter(
        or_(schedulable_status, Task.id.in_(replan_task_ids)),
        Task.is_external_gate.is_(False),
        not_(Task.id.in_(child_parent_ids)),
    ).options(
        selectinload(Task.project),
        selectinload(Task.milestone),
        selectinload(Task.predecessors),
        selectinload(Task.capability_requirements),
    )
    if project_ids:
        query = query.filter(Task.project_id.in_(project_ids))
    if task_ids:
        query = query.filter(Task.id.in_(task_ids))
    if excluded_task_ids:
        query = query.filter(~Task.id.in_(excluded_task_ids))
    tasks = query.order_by(Task.priority_weight.desc(), Task.created_at, Task.id).all()
    # 凡是有任务参与本次求解的项目（含被插单挤动的低优先级项目），其签批后
    # 工时都必须一并计入，否则被挪动的项目会因为这段工时不可见而显得按期。
    occupancy_tasks = _load_unapproved_downstream_tasks(
        db,
        occupancy_project_ids={
            *(occupancy_project_ids or ()),
            *(task.project_id for task in tasks),
        },
        loaded_task_ids={task.id for task in tasks},
        excluded_task_ids=excluded_task_ids,
    )
    tasks.extend(occupancy_tasks)

    instruments = db.query(Instrument).filter(
        Instrument.availability_status == "available",
        Instrument.status.in_(["idle", "running", "fault"]),
    ).options(
        selectinload(Instrument.capabilities),
        selectinload(Instrument.faults),
        selectinload(Instrument.maintenance_windows),
    ).all()
    return tasks, instruments


# 项目一旦收尾，其未签批的下游任务不再占用产能。
INACTIVE_PROJECT_STATUSES = ("completed", "cancelled", "archived")


def _load_unapproved_downstream_tasks(
    db,
    occupancy_project_ids: set[int] | None,
    loaded_task_ids: set[int],
    excluded_task_ids: set[int] | None = None,
):
    """载入本次排程所涉项目中未签批的下游任务，用于占用仪器与人员产能。

    这些任务签批前不落地时间槽，若不参与求解，它们的工时在签批前完全不可见，
    项目完工时间与延期判断都会系统性偏乐观。

    范围是"本次求解实际涉及的项目"，而不是全厂：与本次排程无关的项目若也
    放不下自己的签批后工时，会把整个模型压成不可解，让任何项目都排不了程。
    那部分跨项目占用改由 load_diagnostic_resource_tasks 在失败诊断中呈现，
    不作为硬约束。
    """
    if not occupancy_project_ids:
        return []
    query = db.query(Task).join(Project, Task.project_id == Project.id).filter(
        Task.project_id.in_(occupancy_project_ids),
        Task.status == "waiting_external",
        Task.is_external_gate.is_(False),
        Task.predecessors.any(),
        Project.status.notin_(INACTIVE_PROJECT_STATUSES),
    ).options(
        selectinload(Task.project),
        selectinload(Task.milestone),
        selectinload(Task.predecessors),
        selectinload(Task.capability_requirements),
    )
    if loaded_task_ids:
        query = query.filter(~Task.id.in_(loaded_task_ids))
    if excluded_task_ids:
        query = query.filter(~Task.id.in_(excluded_task_ids))
    return query.order_by(Task.created_at, Task.id).all()


def load_task_children(db, tasks) -> dict[int, list[int]]:
    project_ids = {task.project_id for task in tasks}
    if not project_ids:
        return {}
    rows = db.query(Task.id, Task.parent_id).filter(
        Task.project_id.in_(project_ids),
        Task.parent_id.isnot(None),
    ).all()
    children_by_parent: dict[int, list[int]] = {}
    for task_id, parent_id in rows:
        children_by_parent.setdefault(parent_id, []).append(task_id)
    return children_by_parent


def load_diagnostic_resource_tasks(
    db,
    excluded_task_ids: set[int] | None = None,
    current_project_id: int | None = None,
):
    """Load resource work omitted from the solver input for failure diagnostics."""
    conditions = [
        Task.requires_instrument.is_(True),
        Task.status.notin_(["done", "completed"]),
    ]
    # 等待签批的下游任务（方法验证等）还没有时间槽，但签批通过后一定会占用
    # 仪器。只按"有没有时间槽"来判定，会让占用分析里其他项目的预测工时恒为 0。
    occupies_resource = or_(Task.time_slots.any(), Task.status == "waiting_external")
    if current_project_id is None:
        conditions.append(occupies_resource)
    else:
        # 保留其他项目已有的资源占用，并纳入当前项目等待签批的后续任务。
        conditions.append(or_(occupies_resource, Task.project_id == current_project_id))
    query = db.query(Task).filter(*conditions).options(
        selectinload(Task.project),
        selectinload(Task.time_slots),
        selectinload(Task.predecessors),
        selectinload(Task.capability_requirements),
    )
    if excluded_task_ids:
        query = query.filter(~Task.id.in_(excluded_task_ids))
    return query.all()


def load_bridge_candidate_tasks(db, project_ids: set[int]):
    """加载可能"桥接"仪器的非仪器任务。

    非仪器任务夹在两个"同仪器 + 同负责人"的任务之间时，期间仪器不会被释放。
    这些任务不出现在仪器占用取数里，需要单独加载才能算出桥接占用。
    """
    if not project_ids:
        return []
    return db.query(Task).filter(
        Task.project_id.in_(project_ids),
        Task.requires_instrument.is_(False),
        Task.requires_human.is_(True),
        Task.assignee_id.isnot(None),
        Task.is_external_gate.is_(False),
        Task.status.notin_(["done", "completed"]),
    ).options(
        selectinload(Task.project),
        selectinload(Task.time_slots),
        selectinload(Task.predecessors),
    ).all()
