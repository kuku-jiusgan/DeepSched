from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ProjectHealthSummary(BaseModel):
    project_status: str
    health_score: int
    health_level: Literal["green", "yellow", "red"]
    delivery_status: Literal["on_time", "at_risk", "overdue"]
    due_date: datetime | None
    predicted_end: datetime | None
    days_delta: int
    schedule_state: Literal["not_scheduled", "scheduled", "dirty", "executing", "completed"]
    metric_mode: Literal["estimated_hours", "task_count"]
    task_counts: dict[str, int]


class HealthTaskItem(BaseModel):
    task_id: int
    task_name: str
    status: str
    plan_start: datetime | None
    plan_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    delay_days: float
    delay_reason: str | None
    assignee_name: str | None


class HealthBlocker(HealthTaskItem):
    blocker_type: Literal["delayed", "unscheduled", "waiting_external"]


class HealthTimelinePoint(BaseModel):
    date: datetime
    ideal: float
    actual: float
    forecast: float


class HealthTimelineAnnotation(BaseModel):
    date: datetime
    title: str
    detail: str
    task_id: int | None


class HealthTimelineTask(BaseModel):
    task_id: int
    task_name: str
    status: str
    plan_start: datetime | None
    plan_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    assignee_name: str | None
    is_external_gate: bool
    expected_approval_at: datetime | None


class ProjectArrangementItem(BaseModel):
    slot_id: int | None
    task_id: int
    task_name: str
    top_level_task_name: str | None
    plan_order: int
    task_status: str
    slot_status: str | None
    delay_status: str
    assignee_id: int | None
    assignee_name: str | None
    instrument_id: int | None
    instrument_code: str | None
    instrument_name: str | None
    plan_start: datetime | None
    plan_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    is_external_gate: bool
    expected_approval_at: datetime | None


class ProjectHealthTimeline(BaseModel):
    total_value: float
    points: list[HealthTimelinePoint]
    annotations: list[HealthTimelineAnnotation]
    tasks: list[HealthTimelineTask]


class ProjectHealthOut(BaseModel):
    project_id: int
    project_code: str
    project_name: str
    client_name: str | None
    manager_name: str | None
    start_date: datetime | None
    end_date: datetime | None
    summary: ProjectHealthSummary
    due_this_week_open: list[HealthTaskItem]
    delayed_over_three_days: list[HealthTaskItem]
    blockers: list[HealthBlocker]
    timeline: ProjectHealthTimeline
    arrangement_items: list[ProjectArrangementItem]
