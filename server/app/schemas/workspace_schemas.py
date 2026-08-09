from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskWindowOut(BaseModel):
    start: datetime | None = None
    end: datetime | None = None


class WorkspaceSegmentOut(BaseModel):
    id: int
    instrument_id: int | None = None
    instrument_name: str | None = None
    instrument_code: str | None = None
    plan_start: datetime
    plan_end: datetime
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    tier: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class WorkspaceDelayOut(BaseModel):
    status: Literal["delayed", "not_delayed"] = "not_delayed"
    hours: float | None = None
    reason: str | None = None
    reported_at: datetime | None = None


class WorkspaceResumePriorityOut(BaseModel):
    task_id: int
    task_name: str
    project_id: int | None = None
    project_name: str | None = None
    project_code: str | None = None


class WorkspaceTaskOut(BaseModel):
    task_id: int
    task_name: str | None = None
    top_level_task_name: str | None = None
    task_type: str | None = None
    assignee_id: int | None = None
    assignee_name: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    project_code: str | None = None
    execution_status: str
    est_duration_hours: float | None = None
    actual_duration_hours: float | None = None
    task_window: TaskWindowOut
    actual_window: TaskWindowOut
    actionable_slot: WorkspaceSegmentOut | None = None
    segments: list[WorkspaceSegmentOut] = Field(default_factory=list)
    delay: WorkspaceDelayOut
    resume_priority: WorkspaceResumePriorityOut | None = None


class AgendaAssigneeOut(BaseModel):
    id: int
    display_name: str


class AgendaItemOut(BaseModel):
    slot_id: int
    task_id: int
    task_name: str
    top_level_task_name: str | None = None
    task_status: str
    slot_status: str
    execution_status: str
    project_id: int
    project_code: str
    project_name: str
    instrument_id: int | None = None
    instrument_code: str | None = None
    instrument_name: str | None = None
    plan_start: datetime
    plan_end: datetime
    task_plan_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None


class AgendaOut(BaseModel):
    start_date: date
    end_date: date
    assignee: AgendaAssigneeOut
    can_select_assignee: bool
    items: list[AgendaItemOut] = Field(default_factory=list)
