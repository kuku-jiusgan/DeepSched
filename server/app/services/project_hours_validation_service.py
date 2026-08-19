from app.models import Project, Task
import logging


_logger = logging.getLogger(__name__)


class ProjectHoursExceededError(Exception):
    pass


def validate_project_estimated_hours(db, project_id: int) -> None:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or project.estimated_hours is None:
        return

    total_hours = project_top_level_task_hours(db, project_id)
    _logger.warning(
        "项目工时校验: project_id=%s code=%s estimated_hours=%s "
        "top_level_hours=%s",
        project.id,
        project.code,
        project.estimated_hours,
        total_hours,
    )
    if total_hours <= float(project.estimated_hours):
        return

    raise ProjectHoursExceededError(
        f"项目任务总耗时 {format_hours(total_hours)}h 已超过项目预计工时 "
        f"{format_hours(float(project.estimated_hours))}h"
    )


def validate_projects_estimated_hours(db, project_ids: set[int]) -> None:
    for project_id in sorted(project_ids):
        validate_project_estimated_hours(db, project_id)


def project_top_level_task_hours(db, project_id: int) -> float:
    tasks = db.query(Task).filter(
        Task.project_id == project_id,
        Task.parent_id.is_(None),
    ).all()
    return sum(float(task.est_duration_hours or 0) for task in tasks)


def format_hours(value: float) -> str:
    return f"{value:g}"
