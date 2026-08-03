from __future__ import annotations

from datetime import datetime

from app.models import Instrument, InstrumentFault, Task, TimeSlot
from app.services.push_notification_service import push_by_rule


ACTIVE_SLOT_STATUSES = ["scheduled", "running", "paused", "blocked", "interrupted"]


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
    impact = evaluate_fault_impact(db, instrument, reported_at, estimated_resolved_at)
    violations = [task for task in impact["affected_task_details"] if not task["can_shift"]]
    if violations:
        _notify_project_managers(db, instrument, violations, estimated_resolved_at)
        first = violations[0]
        raise InstrumentFaultScheduleConflict(first["reason"], impact)

    affected_slots = _affected_slots(db, instrument.id, reported_at)
    if not affected_slots:
        return impact

    first_start = min(slot.plan_start for slot in affected_slots)
    shift_to = max(estimated_resolved_at, first_start)
    delay = shift_to - first_start
    if delay.total_seconds() <= 0:
        return impact

    task_ids = {slot.task_id for slot in affected_slots}
    tasks = {
        task.id: task
        for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
    }

    for slot in affected_slots:
        slot.plan_start = slot.plan_start + delay
        slot.plan_end = slot.plan_end + delay
        if slot.status == "running":
            slot.status = "scheduled"
            slot.actual_start = None
            slot.actual_end = None
        task = tasks.get(slot.task_id)
        if task and task.status == "running":
            task.status = "scheduled"

    notified_users = _notify_assignees(
        db,
        instrument,
        tasks.values(),
        estimated_resolved_at,
        len(affected_slots),
    )
    return {
        "shifted_slots": len(affected_slots),
        "affected_tasks": len(task_ids),
        "notified_users": notified_users,
        "affected_task_details": impact["affected_task_details"],
    }


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
        return _impact([], 0, 0, 0)

    first_start = min(slot.plan_start for slot in affected_slots)
    shift_to = max(estimated_resolved_at, first_start)
    delay = shift_to - first_start
    task_ids = {slot.task_id for slot in affected_slots}
    tasks = {
        task.id: task
        for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
    }
    details = []
    for task_id in task_ids:
        task = tasks.get(task_id)
        task_slots = [slot for slot in affected_slots if slot.task_id == task_id]
        if not task or not task_slots:
            continue
        original_start = min(slot.plan_start for slot in task_slots)
        original_end = max(slot.plan_end for slot in task_slots)
        shifted_start = original_start + delay
        shifted_end = original_end + delay
        can_shift = True
        reason = ""
        if task.project and task.project.end_date and shifted_end > task.project.end_date:
            can_shift = False
            reason = (
                f"仪器预计维修完成时间超过项目【{task.project.name}】结束日期，"
                f"任务【{task.name}】无法后移排程。"
            )
        details.append({
            "task_id": task.id,
            "task_name": task.name,
            "project_id": task.project_id,
            "project_name": task.project.name if task.project else None,
            "project_code": task.project.code if task.project else None,
            "assignee_name": task.assignee.display_name if task.assignee else None,
            "original_start": original_start.isoformat(),
            "original_end": original_end.isoformat(),
            "shifted_start": shifted_start.isoformat(),
            "shifted_end": shifted_end.isoformat(),
            "can_shift": can_shift,
            "reason": reason,
        })
    details.sort(key=lambda item: item["original_start"])
    return _impact(details, len(affected_slots), len(task_ids), 0)


def _affected_slots(db, instrument_id: int, reported_at: datetime) -> list[TimeSlot]:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.instrument_id == instrument_id,
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
            TimeSlot.plan_end > reported_at,
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )


def _impact(
    affected_task_details: list[dict],
    shifted_slots: int,
    affected_tasks: int,
    notified_users: int,
) -> dict:
    return {
        "shifted_slots": shifted_slots,
        "affected_tasks": affected_tasks,
        "notified_users": notified_users,
        "affected_task_details": affected_task_details,
    }


def _notify_assignees(
    db,
    instrument: Instrument,
    tasks,
    estimated_resolved_at: datetime,
    shifted_slots: int,
) -> int:
    notified = 0
    task_names = "、".join(task.name for task in tasks if task is not None)
    notify_users = []
    for task in tasks:
        if not task or not task.assignee or not task.assignee.username:
            continue
        notify_users.append(task.assignee)
    if notify_users:
        notified = push_by_rule(
            db,
            "instrument_fault_reschedule",
            notify_users,
            f"{instrument.name} 故障影响排程",
            (
                f"仪器 {instrument.name}({instrument.code}) 已提报故障，"
                f"预计维修完成时间为 {estimated_resolved_at:%Y-%m-%d %H:%M}。"
                f"受影响任务：{task_names or '暂无'}，已后移 {shifted_slots} 个时间槽。"
            ),
            related_entity_type="instrument",
            related_entity_id=instrument.id,
        )
    return notified


def _notify_project_managers(
    db,
    instrument: Instrument,
    violations: list[dict],
    estimated_resolved_at: datetime,
) -> int:
    notified = 0
    notify_items = []
    for violation in violations:
        task = db.query(Task).filter(Task.id == violation["task_id"]).first()
        manager = task.project.manager if task and task.project else None
        if not manager or not manager.username:
            continue
        notify_items.append((manager, violation))
    for manager, violation in notify_items:
        notified += push_by_rule(
            db,
            "instrument_fault_schedule_conflict",
            [manager],
            f"{instrument.name} 故障导致排程冲突",
            (
                f"仪器 {instrument.name}({instrument.code}) 预计维修完成时间为 "
                f"{estimated_resolved_at:%Y-%m-%d %H:%M}。{violation['reason']}"
            ),
            related_entity_type="task",
            related_entity_id=violation["task_id"],
            context_roles=["项目负责人"],
        )
    return notified
