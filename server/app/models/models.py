from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, Text, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.database import Base

class Project(Base):
    __tablename__ = "project"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    client_name = Column(String(200))
    estimated_hours = Column(Float)
    priority = Column(Integer, default=3)
    status = Column(String(20), default="pending")
    project_kind = Column(String(20), nullable=False, default="project", comment="project/detection")
    manager_id = Column(Integer, ForeignKey("user.id"))
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    milestones = relationship("Milestone", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    manager = relationship("User")
    @property
    def manager_name(self):
        return self.manager.display_name if self.manager else None

class Milestone(Base):
    __tablename__ = "milestone"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    name = Column(String(200), nullable=False)
    due_date = Column(DateTime, nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=datetime.now)

    project = relationship("Project", back_populates="milestones")

class Task(Base):
    __tablename__ = "task"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    name = Column(String(300), nullable=False)
    task_type = Column(String(20), nullable=False)
    requires_instrument = Column(Boolean, default=False)
    requires_human = Column(Boolean, default=True)
    est_duration_hours = Column(Float)
    switchover_hours = Column(Float, default=0)
    allow_split = Column(Boolean, default=False)
    allow_transfer = Column(Boolean, default=False)
    milestone_id = Column(Integer, ForeignKey("milestone.id"))
    priority_weight = Column(Float, default=1.0)
    instrument_ids = Column(JSON, default=[], comment="可选仪器ID列表")
    status = Column(String(20), default="pending")
    delay_status = Column(String(20), default="not_delayed")
    schedule_dirty = Column(Boolean, default=False)
    parent_id = Column(Integer, ForeignKey("task.id"), nullable=True)
    plan_order = Column(Integer, default=0, nullable=False, comment="项目计划同级排序")
    assignee_id = Column(Integer, ForeignKey("user.id"))
    is_external_gate = Column(Boolean, default=False)
    gate_status = Column(String(30), default="not_submitted")
    expected_approval_at = Column(DateTime)
    submitted_at = Column(DateTime)
    approved_at = Column(DateTime)
    approved_by = Column(Integer, ForeignKey("user.id"))
    approval_note = Column(Text)
    approval_schedule_status = Column(String(30))
    approval_schedule_message = Column(Text)
    approval_preview_token = Column(String(128))
    approval_schedule_run_id = Column(String(64))
    approval_moved_tasks = Column(Integer, default=0)
    assignee = relationship("User", foreign_keys=[assignee_id])
    approved_by_user = relationship("User", foreign_keys=[approved_by])
    parent = relationship("Task", remote_side=[id], back_populates="children")
    children = relationship("Task", back_populates="parent", cascade="all, delete-orphan")
    earliest_start = Column(DateTime)
    latest_due = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    project = relationship("Project", back_populates="tasks")
    milestone = relationship("Milestone")

    @property
    def assignee_name(self):
        return self.assignee.display_name if self.assignee else None

    @property
    def predecessor_ids(self):
        return [d.predecessor_id for d in self.predecessors] if self.predecessors else []

    @property
    def schedule_lock_status(self):
        if self.status in {"done", "completed"} or any(slot.status == "completed" for slot in self.time_slots):
            return "completed"
        if self.status == "running" or any(
            slot.status == "running" or (slot.actual_start is not None and slot.actual_end is None)
            for slot in self.time_slots
        ):
            return "running"
        if any(slot.tier == "frozen" for slot in self.time_slots):
            return "frozen"
        return "none"

    @property
    def can_edit_schedule_fields(self):
        return self.schedule_lock_status == "none"

    @property
    def can_edit_basic_fields(self):
        return self.schedule_lock_status != "completed"

    @property
    def can_edit_schedule_window(self):
        return self.schedule_lock_status != "completed"

    @property
    def can_edit_resource_fields(self):
        return self.schedule_lock_status == "none"
    predecessors = relationship("TaskDependency", foreign_keys="TaskDependency.task_id", back_populates="task", cascade="all, delete-orphan")
    capability_requirements = relationship("TaskCapabilityRequirement", back_populates="task", cascade="all, delete-orphan")
    time_slots = relationship("TimeSlot", back_populates="task", cascade="all, delete-orphan")
    execution_segments = relationship("TaskExecutionSegment", back_populates="task", cascade="all, delete-orphan")

class TaskDependency(Base):
    __tablename__ = "task_dependency"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    predecessor_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    __table_args__ = (UniqueConstraint("task_id", "predecessor_id"),)

    task = relationship("Task", foreign_keys=[task_id], back_populates="predecessors")
    predecessor = relationship("Task", foreign_keys=[predecessor_id])


class TaskCapabilityRequirement(Base):
    __tablename__ = "task_capability_requirement"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    tag_name = Column(String(100), nullable=False)
    tag_value = Column(String(200), nullable=False)

    task = relationship("Task", back_populates="capability_requirements")

class Instrument(Base):
    __tablename__ = "instrument"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    instrument_group = Column(String(50), default="GTI_Group")
    brand = Column(String(50))
    model = Column(String(100))
    location = Column(String(50))
    availability_status = Column(String(20), default="available")
    status = Column(String(20), default="idle")
    buffer_rate = Column(Float, default=1.1)
    switchover_base_hours = Column(Float, default=0.5)
    label_x = Column(Integer, default=0)
    label_y = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)

    capabilities = relationship("InstrumentCapability", back_populates="instrument", cascade="all, delete-orphan")
    maintenance_windows = relationship("MaintenanceWindow", back_populates="instrument", cascade="all, delete-orphan")
    faults = relationship("InstrumentFault", back_populates="instrument", cascade="all, delete-orphan")
    time_slots = relationship("TimeSlot", back_populates="instrument", cascade="all, delete-orphan")

class InstrumentCapability(Base):
    __tablename__ = "instrument_capability"
    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, ForeignKey("instrument.id"), nullable=True)
    tag_name = Column(String(100), nullable=False)
    tag_value = Column(String(200), nullable=False)

    instrument = relationship("Instrument", back_populates="capabilities")

class MaintenanceWindow(Base):
    __tablename__ = "maintenance_window"
    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, ForeignKey("instrument.id"), nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    mw_type = Column(String(30), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    instrument = relationship("Instrument", back_populates="maintenance_windows")

class InstrumentFault(Base):
    __tablename__ = "instrument_fault"
    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, ForeignKey("instrument.id"), nullable=True)
    reported_at = Column(DateTime, default=datetime.now)
    estimated_resolved_at = Column(DateTime)
    resolved_at = Column(DateTime)
    description = Column(Text)
    status = Column(String(20), default="open")

    instrument = relationship("Instrument", back_populates="faults")

class TimeSlot(Base):
    __tablename__ = "time_slot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_run_id = Column(String(64), nullable=False, default="legacy")
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    instrument_id = Column(Integer, ForeignKey("instrument.id"), nullable=True)
    plan_start = Column(DateTime, nullable=False)
    plan_end = Column(DateTime, nullable=False)
    actual_start = Column(DateTime)
    actual_end = Column(DateTime)
    is_night_run = Column(Boolean, default=False, nullable=False)
    tier = Column(String(10), nullable=False, default="forecast")
    status = Column(String(20), default="scheduled")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    task = relationship("Task", back_populates="time_slots")
    instrument = relationship("Instrument", back_populates="time_slots")


class TaskExecutionSegment(Base):
    __tablename__ = "task_execution_segment"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("time_slot.id"), nullable=False)
    instrument_id = Column(Integer, ForeignKey("instrument.id"))
    operator_id = Column(Integer, ForeignKey("user.id"))
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime)
    end_reason = Column(String(20))
    pause_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    task = relationship("Task", back_populates="execution_segments")
    slot = relationship("TimeSlot")
    instrument = relationship("Instrument")
    operator = relationship("User")


class TaskNightRun(Base):
    __tablename__ = "task_night_run"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("time_slot.id"), nullable=False)
    instrument_id = Column(Integer, ForeignKey("instrument.id"), nullable=False)
    operator_id = Column(Integer, ForeignKey("user.id"))
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    __table_args__ = (UniqueConstraint("task_id", "instrument_id", "started_at"),)

    task = relationship("Task")
    slot = relationship("TimeSlot")
    instrument = relationship("Instrument")
    operator = relationship("User")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_name = Column(String(100), nullable=False)
    action = Column(String(50), nullable=False)
    target_type = Column(String(50), nullable=False)
    target_id = Column(Integer)
    detail = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)



class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    role = Column(String(30), nullable=False, comment="系统管理员/项目管理员/分析所所长/技术组长/技术员")
    roles = Column(JSON, nullable=True, comment="用户同时拥有的角色列表")
    email = Column(String(100))
    phone = Column(String(20))
    wecom_id = Column(String(100), comment="企业微信号")
    password_hash = Column(String(128))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class RolePermission(Base):
    __tablename__ = "role_permission"
    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(30), nullable=False)
    page_key = Column(String(100), nullable=False)
    can_view = Column(Boolean, nullable=False, default=False)
    can_operate = Column(Boolean, nullable=False, default=False)
    action_permissions = Column(JSON, nullable=True, comment="页面按钮权限映射")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    __table_args__ = (UniqueConstraint("role", "page_key"),)

class ScheduleRule(Base):
    __tablename__ = "schedule_rule"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(30), nullable=False, comment="决策变量/约束条件/目标函数")
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    description = Column(Text)
    params = Column(JSON, comment="可配置参数")
    is_enabled = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class Notification(Base):
    __tablename__ = "notification"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_name = Column(String(100), nullable=False)
    n_type = Column(String(30), nullable=False)
    channel = Column(String(20), default="site")
    delivery_status = Column(String(20), default="success")
    error_message = Column(Text)
    title = Column(String(300))
    content = Column(Text)
    related_entity_type = Column(String(50))
    related_entity_id = Column(Integer)
    is_read = Column(Boolean, default=False)
    is_confirmed = Column(Boolean)
    created_at = Column(DateTime, default=datetime.now)

class TaskTypeConfig(Base):
    __tablename__ = "task_type_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="任务类型名称")
    code = Column(String(50), unique=True, nullable=False, comment="类型编码")
    resource_type = Column(String(20), nullable=False, default="both", comment="资源依赖: instrument/human/both")
    description = Column(Text, comment="描述")
    is_active = Column(Boolean, default=True, comment="是否启用")
    sort_order = Column(Integer, default=0, comment="排序")
    predecessor_type_ids = Column(JSON, default=[], comment="前置任务类型ID列表")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class AlertRule(Base):
    __tablename__ = "alert_rule"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    rule_type = Column(String(30), nullable=False, comment="task_start_delay/task_end_delay/schedule_changed/hours_exceeded")
    enabled = Column(Boolean, default=True)
    enable_site = Column(Boolean, default=True)
    enable_wecom = Column(Boolean, default=True)
    notify_roles = Column(Text, comment="JSON array of roles")
    threshold_minutes = Column(Integer, default=30)
    threshold_percent = Column(Integer, default=120)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AuthSession(Base):
    __tablename__ = "auth_session"
    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    username = Column(String(50), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_seen_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    user = relationship("User")


class WeComOAuthState(Base):
    __tablename__ = "wecom_oauth_state"
    state_hash = Column(String(64), primary_key=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class LoginFailure(Base):
    __tablename__ = "login_failure"
    key = Column(String(200), primary_key=True)
    failure_count = Column(Integer, default=0, nullable=False)
    first_failed_at = Column(DateTime, nullable=False)
    locked_until = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class WorkerLease(Base):
    __tablename__ = "worker_lease"
    name = Column(String(100), primary_key=True)
    owner_id = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

class PushChannelConfig(Base):
    __tablename__ = "push_channel_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    wecom_enabled = Column(Boolean, default=True)
    wecom_corp_id = Column(String(200))
    wecom_agent_id = Column(String(50))
    wecom_secret = Column(String(300))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class SysCalendar(Base):
    __tablename__ = "sys_calendar"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True, comment="日期")
    is_working_day = Column(Boolean, default=True, comment="是否工作日")
    holiday_name = Column(String(100), comment="假期名称（如：春节、国庆节）")
    day_type = Column(String(20), default="workday", comment="workday/weekend/holiday/compensate")
    source = Column(String(20), nullable=False, default="default", comment="default/sync/manual")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ScheduleCalendarSnapshot(Base):
    __tablename__ = "schedule_calendar_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_run_id = Column(String(64), nullable=False, unique=True)
    horizon_start = Column(DateTime, nullable=False)
    horizon_end = Column(DateTime, nullable=False)
    working_hours = Column(JSON, nullable=False)
    calendar_days = Column(JSON, nullable=False)
    maintenance_windows = Column(JSON, nullable=False)
    rule_versions = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
