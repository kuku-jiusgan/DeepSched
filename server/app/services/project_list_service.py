from sqlalchemy import func, case

from app.models import Project, Task, TimeSlot
from app.services.project_status_service import COMPLETED_TASK_STATUSES


def project_status_map(db, project_ids: set[int]) -> dict[int, str]:
    if not project_ids:
        return {}
    parent_ids = {
        parent_id for (parent_id,) in db.query(Task.parent_id).filter(
            Task.project_id.in_(project_ids), Task.parent_id.isnot(None),
        ).distinct().all()
    }
    leaf_filter = [Task.project_id.in_(project_ids)]
    if parent_ids:
        leaf_filter.append(~Task.id.in_(parent_ids))
    rows = db.query(
        Task.project_id,
        func.count(Task.id).label("total"),
        func.sum(case((Task.status.in_(COMPLETED_TASK_STATUSES), 1), else_=0)).label("completed"),
        func.sum(case((Task.status.in_(["running", "paused", "done", "completed", "interrupted"]), 1), else_=0)).label("started"),
    ).filter(*leaf_filter).group_by(Task.project_id).all()
    started_by_slot = {
        project_id for (project_id,) in db.query(Task.project_id).join(
            TimeSlot, TimeSlot.task_id == Task.id,
        ).filter(
            Task.project_id.in_(project_ids),
            TimeSlot.actual_start.isnot(None),
            *([~Task.id.in_(parent_ids)] if parent_ids else []),
        ).distinct().all()
    }
    result = {project_id: "pending" for project_id in project_ids}
    for row in rows:
        if row.total and row.completed == row.total:
            result[row.project_id] = "completed"
        elif row.started or row.project_id in started_by_slot:
            result[row.project_id] = "active"
    return result
