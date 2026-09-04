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
    instrument_codes: list[str]
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    schedule_judgement: str
    delay_hours: float
    night_run_hours: float = 0.0
    pause_count: int
    pause_reasons: list[str]


class ProjectHoursItemOut(BaseModel):
    project_id: int
    project_kind: str
    project_code: str
    project_name: str
    client_name: str | None = None
    manager_name: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    project_status: str
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
