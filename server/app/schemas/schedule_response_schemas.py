from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ProjectScheduleImpact(BaseModel):
    project_id: int
    project_code: str
    project_name: str
    project_end_date: Optional[datetime] = None
    original_start: Optional[datetime] = None
    new_start: Optional[datetime] = None
    original_completion: Optional[datetime] = None
    new_completion: Optional[datetime] = None
    delay_hours: float = 0
    exceeds_end_date: bool = False
    overdue_hours: float = 0
    pending_approval_hours: float = 0


class InsertOrderImpact(BaseModel):
    task_id: int
    task_name: str
    project_id: int
    project_name: str
    is_insert_task: bool = False
    original_start: Optional[datetime] = None
    original_end: Optional[datetime] = None
    new_start: datetime
    new_end: datetime
    delay_hours: float = 0
    impact_role: Optional[Literal["inserted", "anchor_downstream", "source_downstream", "shifted"]] = None


class ProjectPlanApplyResponse(BaseModel):
    status: Literal["queued", "applied", "no_changes", "insert_confirmation_required", "error"]
    message: Optional[str] = None
    project_id: int
    schedule_run_id: Optional[str] = None
    timeslots_created: int = 0
    moved_tasks: int = 0
    conflicts_checked: bool = False
    preview_token: Optional[str] = None
    impacts: List[InsertOrderImpact] = []
    project_impacts: List[ProjectScheduleImpact] = []
    created: int = 0
    id_map: List[dict] = []
    schedule_failure: Optional[dict] = None
    request_id: Optional[str] = None


class ScheduleDeadlineRecommendationJobResponse(BaseModel):
    id: str
    status: Literal["pending", "running", "completed", "failed", "stale", "inconclusive"]
    recommendation: Optional[dict] = None
    recommendations: List[dict] = []
    elapsed_seconds: Optional[int] = None
    message: Optional[str] = None


class InsertOrderPreview(BaseModel):
    status: str = "ok"
    schedule_run_id: str
    timeslots_created: int = 0
    total_delay_hours: float = 0
    impacts: List[InsertOrderImpact] = []


class InsertOrderResult(BaseModel):
    status: str = "ok"
    schedule_run_id: str
    timeslots_created: int = 0
    moved_tasks: int = 0
    conflicts_checked: bool = False
    impacts: List[InsertOrderImpact] = []
    audit_detail: dict[str, object] = Field(default_factory=dict, exclude=True)


InsertOrderCost = InsertOrderPreview
