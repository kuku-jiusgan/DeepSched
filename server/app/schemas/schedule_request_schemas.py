from datetime import datetime
from typing import Literal

from pydantic import BaseModel


ScheduleRequestStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "superseded",
]


class ScheduleRequestOut(BaseModel):
    id: str
    project_id: int
    request_type: str
    priority: int
    status: ScheduleRequestStatus
    message: str | None = None
    result: dict | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ScheduleRequestCreate(BaseModel):
    project_id: int
    request_type: str = "project_plan"

