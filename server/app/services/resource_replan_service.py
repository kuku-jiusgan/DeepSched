from __future__ import annotations

from datetime import datetime

from app.models import Task, TimeSlot
from app.services.schedule_replan_closure_service import collect_replan_task_ids
from app.services.scheduler import SchedulerService


def replan_resource_closure(
    db,
    seed_task_ids: set[int],
    released_at: datetime,
    current_project_id: int | None = None,
    *,
    earliest_start_bounds: dict[int, datetime] | None = None,
    advance_notification_reason: str = "资源变更重排",
) -> dict:
    """Run the authoritative CP-SAT replan for a resource-impact closure."""
    if not seed_task_ids:
        return {"status": "ok", "message": "没有受影响任务", "timeslots_created": 0}
    seed_tasks = db.query(Task).filter(Task.id.in_(seed_task_ids)).all()
    if not seed_tasks:
        raise ValueError("资源重排没有找到种子任务")
    rows = [(task.assignee_id,) for task in seed_tasks]
    assignee_ids = {value for (value,) in rows if value is not None}
    instrument_rows = db.query(TimeSlot.instrument_id).filter(
        TimeSlot.task_id.in_(seed_task_ids), TimeSlot.instrument_id.isnot(None),
    ).distinct().all()
    closure_ids = collect_replan_task_ids(
        db,
        set(seed_task_ids),
        {value for (value,) in instrument_rows},
        assignee_ids,
        released_at,
    )
    if not closure_ids:
        closure_ids = set(seed_task_ids)
    closure_projects = {
        project_id for (project_id,) in db.query(Task.project_id).filter(
            Task.id.in_(closure_ids),
        ).distinct().all()
    }
    if not closure_projects:
        raise ValueError("资源重排任务没有关联项目")
    current_project_id = current_project_id or seed_tasks[0].project_id
    return SchedulerService(db).generate(
        project_ids=sorted(closure_projects),
        task_ids=sorted(closure_ids),
        current_project_id=current_project_id,
        earliest_start_bounds=earliest_start_bounds,
        advance_notification_reason=advance_notification_reason,
    )
