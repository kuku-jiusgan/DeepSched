from pydantic import BaseModel, Field, ConfigDict
from typing import Literal, Optional, List
from datetime import datetime

# ---- Project ----
class MilestoneCreate(BaseModel):
    name: str
    due_date: datetime

class MilestoneOut(BaseModel):
    id: int
    project_id: int
    name: str
    due_date: datetime
    status: str
    model_config = ConfigDict(from_attributes=True)

class TaskCapabilityReqCreate(BaseModel):
    tag_name: str
    tag_value: str

class TaskCapabilityReqOut(BaseModel):
    id: int
    tag_name: str
    tag_value: str
    model_config = ConfigDict(from_attributes=True)

class TaskCreate(BaseModel):
    name: str
    task_type: str = "instrument"
    requires_instrument: bool = True
    requires_human: bool = True
    est_duration_hours: Optional[float] = None
    switchover_hours: float = 0
    allow_split: bool = False
    allow_transfer: bool = False
    milestone_id: Optional[int] = None
    priority_weight: float = 1.0
    predecessor_ids: List[int] = []
    instrument_ids: List[int] = []
    assignee_id: Optional[int] = None
    parent_id: Optional[int] = None

class TaskUpdate(BaseModel):
    name: Optional[str] = None
    task_type: Optional[str] = None
    requires_instrument: Optional[bool] = None
    requires_human: Optional[bool] = None
    est_duration_hours: Optional[float] = None
    switchover_hours: Optional[float] = None
    allow_split: Optional[bool] = None
    allow_transfer: Optional[bool] = None
    milestone_id: Optional[int] = None
    priority_weight: Optional[float] = None
    predecessor_ids: Optional[List[int]] = None
    instrument_ids: Optional[List[int]] = None
    assignee_id: Optional[int] = None
    parent_id: Optional[int] = None

class TaskReorder(BaseModel):
    parent_id: Optional[int] = None
    task_ids: List[int]

class TaskOut(BaseModel):
    id: int
    project_id: int
    name: str
    task_type: str
    requires_instrument: bool
    requires_human: bool
    est_duration_hours: Optional[float]
    switchover_hours: float
    allow_split: bool = False
    status: str
    delay_status: Literal["delayed", "not_delayed"] = "not_delayed"
    schedule_dirty: bool = False
    schedule_lock_status: Literal["none", "frozen", "running", "completed"] = "none"
    can_edit_schedule_fields: bool = True
    can_edit_basic_fields: bool = True
    can_edit_schedule_window: bool = True
    can_edit_resource_fields: bool = True
    earliest_start: Optional[datetime]
    latest_due: Optional[datetime]
    priority_weight: float
    instrument_ids: List[int] = []
    predecessor_ids: List[int] = []
    assignee_id: Optional[int] = None
    assignee_name: Optional[str] = None
    delay_hours: Optional[float] = None
    delay_reason: Optional[str] = None
    delay_reported_at: Optional[datetime] = None
    parent_id: Optional[int] = None
    plan_order: int = 0
    children: List["TaskOut"] = []
    is_external_gate: bool = False
    gate_status: Optional[str] = None
    expected_approval_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by_name: Optional[str] = None
    approval_note: Optional[str] = None
    approval_schedule_status: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class ProjectCreate(BaseModel):
    name: str
    code: str
    client_name: Optional[str] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    priority: int = Field(default=3, ge=1, le=3)
    manager_id: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

class ProjectOut(BaseModel):
    id: int
    name: str
    code: str
    client_name: Optional[str]
    estimated_hours: Optional[float] = None
    actual_hours: float = 0
    priority: int
    status: str
    manager_id: Optional[int] = None
    manager_name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    project_kind: str = "project"
    tasks: List[TaskOut] = []
    model_config = ConfigDict(from_attributes=True)

class DetectionTaskCreate(BaseModel):
    code: str
    name: str
    client_name: Optional[str] = None
    priority: int = Field(default=3, ge=1, le=3)
    manager_id: Optional[int] = None
    start_date: datetime
    end_date: datetime
    task_type: str = "instrument"
    est_duration_hours: float = Field(gt=0)
    switchover_hours: float = Field(default=0, ge=0)
    requires_instrument: bool = True
    requires_human: bool = True
    allow_split: bool = False
    allow_transfer: bool = False
    instrument_ids: List[int] = []
    assignee_id: int = Field(gt=0)

class DetectionTaskOut(BaseModel):
    id: int
    project_id: int
    code: str
    name: str
    client_name: Optional[str] = None
    priority: int
    manager_id: Optional[int] = None
    manager_name: Optional[str] = None
    start_date: datetime
    end_date: datetime
    task: TaskOut
    actual_hours: float = 0
    schedule_status: Optional[str] = None
    schedule_message: Optional[str] = None
    preview_token: Optional[str] = None

# ---- Instrument ----
class CapabilityCreate(BaseModel):
    tag_name: str
    tag_value: str

class CapabilityOut(BaseModel):
    id: int
    tag_name: str
    tag_value: str
    model_config = ConfigDict(from_attributes=True)

class MaintenanceCreate(BaseModel):
    start_time: datetime
    end_time: datetime
    mw_type: str
    description: Optional[str] = None

class MaintenanceOut(BaseModel):
    id: int
    instrument_id: Optional[int] = None
    start_time: datetime
    end_time: datetime
    mw_type: str
    description: Optional[str]
    model_config = ConfigDict(from_attributes=True)

class FaultCreate(BaseModel):
    description: str
    estimated_resolved_at: datetime
    resolved_at: Optional[datetime] = None

class FaultOut(BaseModel):
    id: int
    instrument_id: Optional[int] = None
    reported_at: datetime
    estimated_resolved_at: Optional[datetime] = None
    resolved_at: Optional[datetime]
    description: str
    status: str
    schedule_impact: Optional[dict] = None
    affected_tasks: List[dict] = []
    model_config = ConfigDict(from_attributes=True)

class InstrumentCreate(BaseModel):
    code: str
    name: str
    instrument_group: str = "GTI_Group"
    brand: Optional[str] = None
    model: Optional[str] = None
    location: Optional[str] = None
    availability_status: str = "available"
    buffer_rate: float = 1.1
    switchover_base_hours: float = 0.5
    capabilities: List[CapabilityCreate] = []

class InstrumentOut(BaseModel):
    id: int
    code: str
    name: str
    instrument_group: str
    brand: Optional[str]
    model: Optional[str]
    location: Optional[str]
    availability_status: str = "available"
    status: str
    buffer_rate: float
    switchover_base_hours: float
    capabilities: List[CapabilityOut] = []
    model_config = ConfigDict(from_attributes=True)

# ---- TimeSlot ----
class TimeSlotOut(BaseModel):
    id: int
    schedule_run_id: Optional[str] = None
    task_id: int
    instrument_id: Optional[int] = None
    plan_start: datetime
    plan_end: datetime
    actual_start: Optional[datetime]
    actual_end: Optional[datetime]
    tier: str
    status: str
    execution_status: str
    task_name: Optional[str] = None
    task_type: Optional[str] = None
    task_status: Optional[str] = None
    delay_status: str = "not_delayed"
    project_code: Optional[str] = None
    project_name: Optional[str] = None
    instrument_name: Optional[str] = None
    instrument_code: Optional[str] = None
    assignee_id: Optional[int] = None
    assignee_name: Optional[str] = None
    project_id: Optional[int] = None
    delay_hours: Optional[float] = None
    delay_reason: Optional[str] = None
    delay_reported_at: Optional[datetime] = None
    delay_started_at: Optional[datetime] = None
    parent_id: Optional[int] = None
    children: List["TaskOut"] = []
    is_external_gate: bool = False
    gate_status: Optional[str] = None
    expected_approval_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by_name: Optional[str] = None
    approval_note: Optional[str] = None
    approval_schedule_status: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class TimeSlotUpdate(BaseModel):
    plan_start: Optional[datetime] = None
    plan_end: Optional[datetime] = None
    instrument_id: Optional[int] = None
    tier: Optional[str] = None

class TaskStatusUpdate(BaseModel):
    status: str
    actual_time: Optional[datetime] = None

class TaskActionResponse(BaseModel):
    status: str
    message: Optional[str] = None

class TaskPauseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    target_slot_id: Optional[int] = None

class TaskSwitchCandidateOut(BaseModel):
    slot_id: int
    task_id: int
    task_name: str
    project_code: str
    project_name: str
    assignee_name: Optional[str] = None
    plan_start: datetime
    plan_end: datetime
    is_paused: bool = False

# ---- Schedule ----
class ScheduleGenerateRequest(BaseModel):
    project_ids: Optional[List[int]] = None
    mode: Literal["normal"] = "normal"

class InsertOrderRequest(BaseModel):
    project_id: int
    task_ids: List[int]
    priority_override: Optional[int] = Field(default=None, ge=1, le=3)
    mode: Literal["priority", "custom_after_task"] = "priority"
    anchor_task_id: Optional[int] = None

class ProjectPlanApplyRequest(BaseModel):
    project_id: int

class ProjectPlanInsertConfirmRequest(BaseModel):
    project_id: int
    preview_token: str

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

class ProjectPlanApplyResponse(BaseModel):
    status: Literal["applied", "no_changes", "insert_confirmation_required", "error"]
    message: Optional[str] = None
    project_id: int
    schedule_run_id: Optional[str] = None
    timeslots_created: int = 0
    moved_tasks: int = 0
    conflicts_checked: bool = False
    preview_token: Optional[str] = None
    impacts: List["InsertOrderImpact"] = []
    project_impacts: List[ProjectScheduleImpact] = []

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

class RescheduleRequest(BaseModel):
    trigger_type: str
    affected_task_id: Optional[int] = None
    affected_instrument_id: Optional[int] = None
    strategy: str = "local"
    description: Optional[str] = None

class TaskDelayRequest(BaseModel):
    delay_hours: float
    reason: str

class TaskDelayResponse(BaseModel):
    status: str
    task_id: int
    slot_id: int
    delay_hours: float
    shifted_slots: int
    affected_tasks: int
    reason: str

class TaskCompleteRequest(BaseModel):
    release_instrument: bool = True

class TaskCompleteResponse(BaseModel):
    status: str
    message: Optional[str] = None
    moved_tasks: int = 0
    released_instrument: bool = True

class NightRunRequest(BaseModel):
    duration_hours: float = Field(gt=0, multiple_of=0.5)
    earliest_start: Optional[str] = None
    latest_end: Optional[str] = None
    requires_operator: bool = False
    remark: Optional[str] = None

# ---- Stats ----
class UtilizationStats(BaseModel):
    instrument_id: Optional[int] = None
    instrument_name: str
    instrument_code: Optional[str] = None
    total_available_hours: float
    scheduled_hours: float
    actual_run_hours: float
    expected_utilization_rate: float
    actual_utilization_rate: float
    utilization_rate: float
    buffer_consumed_rate: float

class DashboardData(BaseModel):
    total_instruments: int
    active_instruments: int
    total_projects: int
    active_projects: int
    avg_utilization: float
    delayed_tasks: int
    buffer_warnings: List[str] = []
    milestone_risks: List[dict] = []

# ---- Notification ----

# ---- Schedule Rule ----

# ---- User ----
class UserCreate(BaseModel):
    username: str
    display_name: str
    password: Optional[str] = None
    role: str = "技术员"
    roles: Optional[list[str]] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    wecom_id: Optional[str] = None
    is_active: bool = True

class UserDirectoryOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    roles: Optional[list[str]] = None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    roles: Optional[list[str]] = None
    email: Optional[str]
    phone: Optional[str]
    wecom_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class ScheduleRuleOut(BaseModel):
    id: int
    category: str
    name: str
    code: str
    description: Optional[str]
    params: Optional[dict]
    is_enabled: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class ScheduleRuleUpdate(BaseModel):
    params: Optional[dict] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None

class NotificationOut(BaseModel):
    id: int
    user_name: str
    n_type: str
    channel: str = "site"
    delivery_status: str = "success"
    error_message: Optional[str] = None
    title: Optional[str]
    content: Optional[str]
    is_read: bool
    is_confirmed: Optional[bool]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
