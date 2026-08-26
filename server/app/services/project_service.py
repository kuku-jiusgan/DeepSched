from app.models import Project
from app.schemas.schemas import ProjectCreate
from app.services.project_date_service import (
    normalize_project_end,
    normalize_project_start,
    validate_project_window,
)


class ProjectCodeExistsError(Exception):
    pass


class ProjectInvalidError(Exception):
    pass


def create_project(db, data: ProjectCreate) -> Project:
    project_code = data.code.strip()
    if db.query(Project).filter(Project.code == project_code).first():
        raise ProjectCodeExistsError(f"项目编号 {project_code} 已存在")

    start_date = normalize_project_start(data.start_date)
    end_date = normalize_project_end(data.end_date)
    try:
        validate_project_window(start_date, end_date)
    except ValueError as exc:
        raise ProjectInvalidError(str(exc)) from exc

    project = Project(
        name=data.name.strip(),
        code=project_code,
        client_name=data.client_name,
        estimated_hours=data.estimated_hours,
        priority=data.priority,
        status="pending",
        manager_id=data.manager_id,
        start_date=start_date,
        end_date=end_date,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project
