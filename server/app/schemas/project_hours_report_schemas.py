from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ProjectHoursTaskOut(BaseModel):
    task_id: int
    parent_id: int | None = None
    task_name: str
    top_level_task_name: str
    assignee_name: str | None = None
    status: str
    depth: int
    planned_hours: float
    actual_hours: float


class ProjectHoursItemOut(BaseModel):
    project_id: int
    project_code: str
    project_name: str
    client_name: str | None = None
    manager_name: str | None = None
    task_count: int
    planned_hours: float
    actual_hours: float
    variance_hours: float
    tasks: list[ProjectHoursTaskOut]


class ProjectHoursReportOut(BaseModel):
    generated_at: datetime
    project_count: int
    planned_hours: float
    actual_hours: float
    items: list[ProjectHoursItemOut]
