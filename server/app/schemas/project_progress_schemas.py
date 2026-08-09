from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ProjectProgressOverview(BaseModel):
    project_id: int
    project_code: str
    project_name: str
    client_name: str | None
    manager_name: str | None
    project_status: str
    delivery_status: Literal["on_time", "at_risk", "overdue"]
    health_level: Literal["green", "yellow", "red"]
    plan_start: datetime | None
    plan_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    actual_started_at: datetime | None
    due_date: datetime | None
    predicted_end: datetime | None
    days_delta: int
    completed_tasks: int
    total_tasks: int


class ProjectProgressList(BaseModel):
    generated_at: datetime
    items: list[ProjectProgressOverview]
