from __future__ import annotations

from sqlalchemy import and_, not_, or_, select
from sqlalchemy.orm import selectinload

from app.models import Instrument, Task


def load_scheduler_data(
    db,
    project_ids=None,
    task_ids=None,
    excluded_task_ids: set[int] | None = None,
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
    query = db.query(Task).filter(
        schedulable_status,
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

    instruments = db.query(Instrument).filter(
        Instrument.availability_status == "available",
        Instrument.status.in_(["idle", "running", "fault"]),
    ).options(
        selectinload(Instrument.capabilities),
        selectinload(Instrument.faults),
        selectinload(Instrument.maintenance_windows),
    ).all()
    return tasks, instruments


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
