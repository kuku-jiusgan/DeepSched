"""Serializable read-only input snapshot for simulated schedule solves."""

from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json


@dataclass(frozen=True)
class ProjectSnapshot:
    id: int
    end_date: datetime | None
    priority: int


@dataclass(frozen=True)
class TaskSnapshot:
    id: int
    project_id: int
    status: str
    duration_hours: float | None
    assignee_id: int | None
    instrument_ids: tuple[int, ...]
    predecessor_ids: tuple[int, ...]
    requires_human: bool = False


@dataclass(frozen=True)
class InstrumentSnapshot:
    id: int
    code: str
    status: str
    availability_status: str
    switchover_base_hours: float
    capability_tags: tuple[tuple[str, str], ...]
    effective_work_start: str
    effective_work_end: str


@dataclass(frozen=True)
class TimeSlotSnapshot:
    id: int
    task_id: int
    instrument_id: int | None
    plan_start: datetime
    plan_end: datetime
    tier: str
    status: str
    lifecycle_status: str
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    task_requires_human: bool = False
    task_assignee_id: int | None = None


@dataclass(frozen=True)
class MaintenanceWindowSnapshot:
    instrument_id: int
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class BridgeReservationSnapshot:
    id: int
    task_id: int
    instrument_id: int
    previous_task_id: int
    following_task_id: int
    plan_start: datetime
    plan_end: datetime


@dataclass(frozen=True)
class CalendarDaySnapshot:
    day: date
    is_working_day: bool
    day_type: str
    holiday_name: str | None


@dataclass(frozen=True)
class ScheduleSnapshot:
    projects: dict[int, ProjectSnapshot]
    tasks: dict[int, TaskSnapshot]
    instruments: dict[int, InstrumentSnapshot]
    time_slots: tuple[TimeSlotSnapshot, ...]
    maintenance_windows: tuple[MaintenanceWindowSnapshot, ...]
    bridge_reservations: tuple[BridgeReservationSnapshot, ...]
    dependencies: tuple[tuple[int, int], ...]
    calendar_days: tuple[CalendarDaySnapshot, ...]
    rule_params: dict[str, dict]
    rule_enabled: dict[str, bool]
    captured_at: datetime

    def fingerprint(self) -> str:
        payload = {
            "projects": [(item.id, item.end_date.isoformat() if item.end_date else None, item.priority)
                         for item in sorted(self.projects.values(), key=lambda value: value.id)],
            "tasks": [(item.id, item.project_id, item.status, item.duration_hours,
                        item.assignee_id, item.instrument_ids, item.predecessor_ids,
                        item.requires_human)
                       for item in sorted(self.tasks.values(), key=lambda value: value.id)],
            "instruments": [(item.id, item.status, item.availability_status, item.switchover_base_hours,
                              item.capability_tags, item.effective_work_start, item.effective_work_end)
                             for item in sorted(self.instruments.values(), key=lambda value: value.id)],
            "slots": [(item.id, item.task_id, item.instrument_id, item.plan_start.isoformat(),
                       item.plan_end.isoformat(), item.tier, item.status, item.lifecycle_status)
                      for item in self.time_slots],
            "dependencies": self.dependencies,
            "maintenance": [(item.instrument_id, item.start_time.isoformat(), item.end_time.isoformat())
                            for item in self.maintenance_windows],
            "slot_execution": [(item.id, item.actual_start.isoformat() if item.actual_start else None,
                                item.actual_end.isoformat() if item.actual_end else None,
                                item.task_requires_human, item.task_assignee_id)
                               for item in self.time_slots],
            "rule_params": self.rule_params,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=list).encode()
        return hashlib.sha256(encoded).hexdigest()

    def with_deadline_overrides(self, overrides: dict[int, datetime]) -> dict[int, datetime | None]:
        """Return an isolated deadline map without mutating the snapshot."""
        deadlines = {project_id: project.end_date for project_id, project in self.projects.items()}
        unknown = set(overrides) - deadlines.keys()
        if unknown:
            raise ValueError(f"方案模拟包含未知项目: {sorted(unknown)}")
        deadlines.update(overrides)
        return deadlines


@dataclass(frozen=True)
class SimulationContext:
    """Self-contained, worker-safe input for one simulated solve."""

    snapshot: ScheduleSnapshot
    deadline_overrides: dict[int, datetime]
    solver_time_limit: float = 5.0
    feasibility_only: bool = True

    def fork(self, deadline_overrides: dict[int, datetime]) -> "SimulationContext":
        """Create an isolated candidate context without mutating this context."""
        normalized = self.snapshot.with_deadline_overrides(deadline_overrides)
        selected = {
            project_id: deadline
            for project_id, deadline in normalized.items()
            if deadline is not None and project_id in deadline_overrides
        }
        return replace(self, deadline_overrides=selected)


def capture_schedule_snapshot(db, project_ids: set[int], task_ids: set[int]) -> ScheduleSnapshot:
    """Read the minimal immutable input used to seed simulation work."""
    from app.models import Instrument, InstrumentBridgeReservation, MaintenanceWindow, Project, ScheduleRule, SysCalendar, Task, TaskDependency, TimeSlot

    projects = {
        project.id: ProjectSnapshot(
            id=project.id,
            end_date=project.end_date,
            priority=int(project.priority or 999),
        )
        for project in db.query(Project).filter(Project.id.in_(project_ids)).all()
    }
    tasks = {
        task.id: TaskSnapshot(
            id=task.id,
            project_id=task.project_id,
            status=task.status,
            duration_hours=task.est_duration_hours,
            assignee_id=task.assignee_id,
            instrument_ids=tuple(int(item) for item in (task.instrument_ids or [])),
            predecessor_ids=tuple(sorted(dep.predecessor_id for dep in task.predecessors)),
            requires_human=bool(task.requires_human),
        )
        for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
    }
    missing_projects = project_ids - projects.keys()
    missing_tasks = task_ids - tasks.keys()
    if missing_projects or missing_tasks:
        raise ValueError(
            f"排程快照数据缺失 projects={sorted(missing_projects)} tasks={sorted(missing_tasks)}"
        )
    instrument_rows = db.query(Instrument).filter(
        Instrument.availability_status == "available",
    ).all()
    instruments = {
        item.id: InstrumentSnapshot(
            id=item.id, code=item.code, status=item.status,
            availability_status=item.availability_status,
            switchover_base_hours=float(item.switchover_base_hours or 0),
            capability_tags=tuple((cap.tag_name, cap.tag_value) for cap in item.capabilities),
            effective_work_start=item.effective_work_start,
            effective_work_end=item.effective_work_end,
        )
        for item in instrument_rows
    }
    slot_rows = db.query(TimeSlot).filter(TimeSlot.lifecycle_status == "active").all()
    time_slots = tuple(
        TimeSlotSnapshot(
            id=slot.id, task_id=slot.task_id, instrument_id=slot.instrument_id,
            plan_start=slot.plan_start, plan_end=slot.plan_end, tier=slot.tier,
            status=slot.status, lifecycle_status=slot.lifecycle_status,
            actual_start=slot.actual_start, actual_end=slot.actual_end,
            task_requires_human=bool(slot.task.requires_human) if slot.task else False,
            task_assignee_id=slot.task.assignee_id if slot.task else None,
        )
        for slot in slot_rows
    )
    dependency_rows = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(task_ids),
    ).all()
    dependencies = tuple(sorted((row.task_id, row.predecessor_id) for row in dependency_rows))
    maintenance_rows = db.query(MaintenanceWindow).all()
    maintenance_windows = tuple(
        MaintenanceWindowSnapshot(row.instrument_id, row.start_time, row.end_time)
        for row in maintenance_rows if row.instrument_id is not None
    )
    bridge_rows = db.query(InstrumentBridgeReservation).all()
    bridge_reservations = tuple(
        BridgeReservationSnapshot(
            row.id, row.task_id, row.instrument_id, row.previous_task_id,
            row.following_task_id, row.plan_start, row.plan_end,
        )
        for row in bridge_rows
    )
    calendar_days = tuple(
        CalendarDaySnapshot(row.date, bool(row.is_working_day), row.day_type, row.holiday_name)
        for row in db.query(SysCalendar).all()
    )
    rule_rows = db.query(ScheduleRule).all()
    rule_params = {
        row.code: dict(row.params or {})
        for row in rule_rows
    }
    rule_enabled = {row.code: bool(row.is_enabled) for row in rule_rows}
    return ScheduleSnapshot(
        projects=projects, tasks=tasks, instruments=instruments,
        time_slots=time_slots, maintenance_windows=maintenance_windows,
        bridge_reservations=bridge_reservations,
        dependencies=dependencies, calendar_days=calendar_days, rule_params=rule_params,
        rule_enabled=rule_enabled,
        captured_at=datetime.now(),
    )
