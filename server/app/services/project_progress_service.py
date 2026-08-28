from datetime import datetime

from app.models import Project
from app.schemas.project_progress_schemas import ProjectProgressList, ProjectProgressOverview
from app.services.project_access_service import list_visible_projects
from app.services.project_health_service import get_project_health
from app.services.project_pending_workload_service import (
    PendingWorkload,
    pending_approval_workload,
)


def list_project_progress(db, user) -> ProjectProgressList:
    projects = list_visible_projects(db, user)
    # 一次性取全部项目的签批后未排工时，避免逐个项目重复查询。
    workloads = pending_approval_workload(db, {project.id for project in projects})
    items = [
        _project_progress(db, project, workloads.get(project.id, PendingWorkload()))
        for project in projects
    ]
    items.sort(key=_progress_sort_key)
    return ProjectProgressList(generated_at=datetime.now(), items=items)


def _project_progress(
    db,
    project: Project,
    pending_workload: PendingWorkload,
) -> ProjectProgressOverview:
    health = get_project_health(db, project, pending_workload)
    tasks = health.timeline.tasks
    complete_actuals = [task for task in tasks if task.actual_start and task.actual_end]
    open_actual_starts = [task.actual_start for task in tasks if task.actual_start and not task.actual_end]
    counts = health.summary.task_counts
    return ProjectProgressOverview(
        project_id=project.id,
        project_code=project.code,
        project_name=project.name,
        client_name=project.client_name,
        manager_name=project.manager_name,
        project_status=health.summary.project_status,
        delivery_status=health.summary.delivery_status,
        health_level=health.summary.health_level,
        plan_start=min((task.plan_start for task in tasks if task.plan_start), default=project.start_date),
        plan_end=max((task.plan_end for task in tasks if task.plan_end), default=project.end_date),
        actual_start=min((task.actual_start for task in complete_actuals), default=None),
        actual_end=max((task.actual_end for task in complete_actuals), default=None),
        actual_started_at=min(open_actual_starts, default=None),
        due_date=health.summary.due_date,
        predicted_end=health.summary.predicted_end,
        days_delta=health.summary.days_delta,
        completed_tasks=counts.get("completed", 0),
        total_tasks=counts.get("total", 0),
    )


def _progress_sort_key(item: ProjectProgressOverview) -> tuple[int, datetime, str]:
    risk_order = {"overdue": 0, "at_risk": 1, "on_time": 2}
    return (
        risk_order[item.delivery_status],
        item.due_date or datetime.max,
        item.project_code,
    )
