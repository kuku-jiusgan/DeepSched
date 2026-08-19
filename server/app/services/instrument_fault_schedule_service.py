from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_

from app.models import Instrument, InstrumentFault, Task, TaskDependency, TimeSlot
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.instrument_fault_notification_service import (
    notify_fault_rescheduled_assignees,
    notify_fault_schedule_risks,
)
from app.services.schedule_advance_notification_service import (
    capture_task_schedule_windows,
    notify_rescheduled_tasks_delayed,
)
from app.services.schedule_delay_service import _load_working_options
from app.services.schedule_forward_slot_service import build_forward_slots


ACTIVE_SLOT_STATUSES = [
    "pending",
    "scheduled",
    "running",
    "paused",
    "blocked",
    "interrupted",
]
ACTIVE_TASK_STATUSES = ["pending", "scheduled", "running", "paused", "blocked", "interrupted"]


class InstrumentFaultScheduleConflict(Exception):
    def __init__(self, message: str, impact: dict):
        super().__init__(message)
        self.impact = impact


def shift_faulted_instrument_slots(
    db,
    instrument: Instrument,
    reported_at: datetime,
    estimated_resolved_at: datetime,
) -> dict:
    affected_slots = _affected_slots(db, instrument.id, reported_at)
    if not affected_slots:
        return _impact([], 0, 0, 0, 0)

    affected_task_ids = _affected_task_ids(db, affected_slots)
    movable_slots = _movable_slots(db, affected_task_ids, reported_at)
    if not movable_slots:
        return _impact([], 0, 0, 0, 0)
    first_start = min(slot.plan_start for slot in movable_slots)
    shift_to = max(estimated_resolved_at, first_start)
    if shift_to <= first_start:
        return evaluate_fault_impact(db, instrument, reported_at, estimated_resolved_at)
    delay_minutes = _rounded_delay_minutes(shift_to - first_start)

    original_windows = capture_task_schedule_windows(
        db,
        {slot.task_id for slot in movable_slots},
    )
    snapshots_by_task = _group_slot_snapshots(movable_slots)
    delete_time_slots_and_refresh(
        db,
        db.query(TimeSlot).filter(TimeSlot.id.in_({slot.id for slot in movable_slots})),
        synchronize_session="fetch",
    )
    db.flush()

    options = _load_working_options(db, reported_at)
    details = []
    for snapshots in sorted(
        snapshots_by_task.values(),
        key=lambda items: (items[0]["plan_start"], items[0]["task_id"]),
    ):
        details.append(
            _restore_shifted_task(
                db,
                snapshots,
                delay_minutes,
                options,
            )
        )

    tasks = _tasks_by_id(db, {detail["task_id"] for detail in details})
    notified_users = notify_fault_rescheduled_assignees(
        db,
        instrument,
        tasks.values(),
        estimated_resolved_at,
        len(movable_slots),
    )
    risk_count = sum(1 for detail in details if not detail["can_shift"])
    if risk_count:
        notify_fault_schedule_risks(
            db,
            instrument,
            [detail for detail in details if not detail["can_shift"]],
            estimated_resolved_at,
        )
    notify_rescheduled_tasks_delayed(
        db,
        original_windows,
        f"仪器“{instrument.name}”故障",
    )
    return _impact(details, len(movable_slots), len(details), notified_users, risk_count)


def fault_affected_tasks(db, fault: InstrumentFault) -> list[dict]:
    if not fault.instrument_id or not fault.estimated_resolved_at:
        return []
    instrument = db.query(Instrument).filter(Instrument.id == fault.instrument_id).first()
    if not instrument:
        return []
    impact = evaluate_fault_impact(
        db,
        instrument,
        fault.reported_at,
        fault.estimated_resolved_at,
    )
    return impact["affected_task_details"]


def evaluate_fault_impact(
    db,
    instrument: Instrument,
    reported_at: datetime,
    estimated_resolved_at: datetime,
) -> dict:
    affected_slots = _affected_slots(db, instrument.id, reported_at)
    if not affected_slots:
        return _impact([], 0, 0, 0, 0)

    affected_task_ids = _affected_task_ids(db, affected_slots)
    slots = _movable_slots(db, affected_task_ids, reported_at)
    if not slots:
        return _impact([], 0, 0, 0, 0)
    first_start = min(slot.plan_start for slot in slots)
    shift_to = max(estimated_resolved_at, first_start)
    delay_minutes = _rounded_delay_minutes(shift_to - first_start)
    snapshots_by_task = _group_slot_snapshots(slots)
    details = [
        _preview_shifted_task(db, snapshots, delay_minutes)
        for snapshots in snapshots_by_task.values()
    ]
    details.sort(key=lambda item: item["original_start"])
    return _impact(details, len(slots), len(details), 0, sum(1 for item in details if not item["can_shift"]))


def _affected_slots(db, instrument_id: int, reported_at: datetime) -> list[TimeSlot]:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.instrument_id == instrument_id,
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
            TimeSlot.plan_end > reported_at,
            TimeSlot.actual_end.is_(None),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )


def _affected_task_ids(db, affected_slots: list[TimeSlot]) -> set[int]:
    task_ids = {slot.task_id for slot in affected_slots}
    return _include_dependency_descendants(db, task_ids)


def _include_dependency_descendants(db, task_ids: set[int]) -> set[int]:
    affected = set(task_ids)
    frontier = set(task_ids)
    while frontier:
        rows = (
            db.query(TaskDependency.task_id)
            .join(Task, Task.id == TaskDependency.task_id)
            .filter(
                TaskDependency.predecessor_id.in_(frontier),
                Task.status.in_(ACTIVE_TASK_STATUSES),
            )
            .distinct()
            .all()
        )
        descendants = {task_id for task_id, in rows} - affected
        affected.update(descendants)
        frontier = descendants
    return affected


def _movable_slots(db, task_ids: set[int], cutoff: datetime) -> list[TimeSlot]:
    if not task_ids:
        return []
    slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id.in_(task_ids),
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
            TimeSlot.actual_end.is_(None),
            TimeSlot.plan_end > cutoff,
            or_(
                TimeSlot.actual_start.is_(None),
                TimeSlot.actual_start >= cutoff,
            ),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    return slots


def _restore_shifted_task(
    db,
    snapshots: list[dict],
    delay_minutes: int,
    options: dict,
) -> dict:
    first_slot = snapshots[0]
    task = db.query(Task).filter(Task.id == first_slot["task_id"]).first()
    duration_minutes = _duration_minutes(snapshots)
    shifted_start = first_slot["plan_start"] + timedelta(minutes=delay_minutes)
    dependency_ready = _dependency_ready_time(db, task)
    if dependency_ready and dependency_ready > shifted_start:
        shifted_start = dependency_ready

    ranges = build_forward_slots(
        db,
        task,
        first_slot["instrument_id"],
        duration_minutes,
        shifted_start,
        options,
        ignore_instrument_free_human_conflicts=bool(
            task and task.requires_instrument
        ),
    )
    if not ranges:
        raise InstrumentFaultScheduleConflict(
            f"仪器故障后无法为任务【{task.name if task else first_slot['task_id']}】找到可用工作时段",
            _impact([], 0, 0, 0, 0),
        )
    status = "scheduled" if first_slot["status"] == "running" else first_slot["status"]
    for start, end in ranges:
        db.add(TimeSlot(
            task_id=first_slot["task_id"],
            schedule_run_id=first_slot["schedule_run_id"],
            instrument_id=first_slot["instrument_id"],
            plan_start=start,
            plan_end=end,
            tier=first_slot["tier"],
            status=status,
        ))
    has_open_execution = bool(
        task and any(segment.ended_at is None for segment in task.execution_segments)
    )
    if task and task.status == "running" and not has_open_execution:
        task.status = "scheduled"
    db.flush()
    return _detail(task, snapshots, ranges[0][0], ranges[-1][1])


def _preview_shifted_task(db, snapshots: list[dict], delay_minutes: int) -> dict:
    task = db.query(Task).filter(Task.id == snapshots[0]["task_id"]).first()
    original_start = min(slot["plan_start"] for slot in snapshots)
    original_end = max(slot["plan_end"] for slot in snapshots)
    shifted_start = original_start + timedelta(minutes=delay_minutes)
    shifted_end = original_end + timedelta(minutes=delay_minutes)
    return _detail(task, snapshots, shifted_start, shifted_end)


def _detail(
    task: Task | None,
    snapshots: list[dict],
    shifted_start: datetime,
    shifted_end: datetime,
) -> dict:
    original_start = min(slot["plan_start"] for slot in snapshots)
    original_end = max(slot["plan_end"] for slot in snapshots)
    reason = ""
    can_shift = True
    if task and task.project and task.project.end_date and shifted_end > task.project.end_date:
        can_shift = False
        reason = (
            f"顺延后超过项目【{task.project.name}】结束日期，"
            f"任务【{task.name}】存在超期风险。"
        )
    return {
        "task_id": task.id if task else snapshots[0]["task_id"],
        "task_name": task.name if task else None,
        "project_id": task.project_id if task else None,
        "project_name": task.project.name if task and task.project else None,
        "project_code": task.project.code if task and task.project else None,
        "assignee_name": task.assignee.display_name if task and task.assignee else None,
        "original_start": original_start.isoformat(),
        "original_end": original_end.isoformat(),
        "shifted_start": shifted_start.isoformat(),
        "shifted_end": shifted_end.isoformat(),
        "can_shift": can_shift,
        "reason": reason,
    }


def _group_slot_snapshots(slots: list[TimeSlot]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for slot in slots:
        grouped.setdefault(slot.task_id, []).append({
            "task_id": slot.task_id,
            "schedule_run_id": slot.schedule_run_id,
            "instrument_id": slot.instrument_id,
            "plan_start": slot.plan_start,
            "plan_end": slot.plan_end,
            "tier": slot.tier,
            "status": slot.status,
        })
    return dict(grouped)


def _dependency_ready_time(db, task: Task | None) -> datetime | None:
    if task is None:
        return None
    predecessor_ids = [dependency.predecessor_id for dependency in task.predecessors]
    if not predecessor_ids:
        return None
    return db.query(TimeSlot.plan_end).filter(
        TimeSlot.task_id.in_(predecessor_ids),
    ).order_by(TimeSlot.plan_end.desc()).limit(1).scalar()


def _duration_minutes(snapshots: list[dict]) -> int:
    return sum(
        max(0, int((slot["plan_end"] - slot["plan_start"]).total_seconds() / 60))
        for slot in snapshots
    )


def _rounded_delay_minutes(delta) -> int:
    seconds = max(0, int(delta.total_seconds()))
    if seconds <= 0:
        return 0
    return max(30, ((seconds + 1799) // 1800) * 30)


def _tasks_by_id(db, task_ids: set[int]) -> dict[int, Task]:
    if not task_ids:
        return {}
    return {
        task.id: task
        for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
    }


def _impact(
    affected_task_details: list[dict],
    shifted_slots: int,
    affected_tasks: int,
    notified_users: int,
    risk_tasks: int,
) -> dict:
    return {
        "shifted_slots": shifted_slots,
        "affected_tasks": affected_tasks,
        "notified_users": notified_users,
        "risk_tasks": risk_tasks,
        "affected_task_details": affected_task_details,
    }
