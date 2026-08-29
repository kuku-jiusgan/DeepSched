from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


GateStatus = Literal["not_submitted", "waiting_approval", "approved"]


class ApprovalGateCreate(BaseModel):
    name: str = "方案签批"
    predecessor_task_id: int
    unlock_task_ids: list[int] = Field(min_length=1)
    assignee_id: int | None = None


class ApprovalGateSubmit(BaseModel):
    expected_approval_at: datetime
    approval_note: str | None = None


class ApprovalGateApprove(BaseModel):
    approval_note: str | None = None


class ApprovalGateTaskRef(BaseModel):
    id: int
    name: str
    status: str | None = None
    completed_at: datetime | None = None
    # 签批解锁的任务在通过之前不落地时间槽，甘特图要在已排时间槽之后把这部分
    # 待排工时显式列出来，否则排程看起来会比真实情况乐观。
    est_duration_hours: float | None = None
    requires_instrument: bool = False
    instrument_ids: list[int] = []
    is_scheduled: bool = False


class ApprovalGateOut(BaseModel):
    id: int
    project_id: int
    project_code: str
    project_name: str
    client_name: str | None = None
    project_manager_id: int | None = None
    project_manager_name: str | None = None
    assignee_id: int | None = None
    assignee_name: str | None = None
    project_end_date: datetime | None = None
    name: str
    top_level_task_name: str | None = None
    gate_status: GateStatus
    expected_approval_at: datetime | None = None
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by_name: str | None = None
    approval_note: str | None = None
    predecessor_tasks: list[ApprovalGateTaskRef] = []
    unlock_tasks: list[ApprovalGateTaskRef] = []
    latest_approval_at: datetime | None = None
    risk_status: Literal["normal", "upcoming", "overdue", "deadline_risk"] = "normal"
    schedule_status: str | None = None
    schedule_message: str | None = None
    schedule_run_id: str | None = None
    preview_token: str | None = None
    moved_tasks: int = 0
    project_expected_completion: datetime | None = None
    can_operate: bool = False


class ApprovalGateListOut(BaseModel):
    items: list[ApprovalGateOut]
    total: int
    page: int
    page_size: int
    pending_count: int
    approved_count: int
    upcoming_count: int
    overdue_count: int


class ApprovalGateActionOut(BaseModel):
    gate: ApprovalGateOut
    schedule_status: str
    schedule_message: str | None = None
    preview_token: str | None = None
