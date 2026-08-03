from __future__ import annotations

from datetime import datetime

from app.models import Instrument, Project, Task, TimeSlot
from app.services.instrument_occupancy_service import (
    ACTIVE_SLOT_STATUSES,
    current_occupying_slot,
)


PROTECTED_INSTRUMENT_STATUSES = {"fault", "maintenance"}


def list_lab_status(db) -> list[dict]:
    instruments = db.query(Instrument).filter(Instrument.availability_status == "available").all()
    now = datetime.now()
    items = [_instrument_status(db, instrument, now) for instrument in instruments]
    if db.dirty:
        db.commit()
    return items


def _instrument_status(db, instrument: Instrument, now: datetime) -> dict:
    current_slot = current_occupying_slot(db, instrument.id)
    status = _reconcile_instrument_status(instrument, current_slot)
    current = _task_status_fields(db, current_slot, now)
    upcoming = _next_task_slot(db, instrument.id, current["task_id"])
    next_fields = _next_task_fields(db, upcoming)
    return {
        "id": instrument.id,
        "code": instrument.code,
        "name": instrument.name,
        "group": instrument.instrument_group,
        "location": instrument.location,
        "status": status,
        "buffer_rate": instrument.buffer_rate,
        "label_x": instrument.label_x or 0,
        "label_y": instrument.label_y or 0,
        "current_task": current["task_name"],
        "current_project": current["project_name"],
        "current_project_code": current["project_code"],
        "current_task_end": current["task_end"],
        "current_user": current["user_name"],
        "progress": current["progress"],
        "next_task": next_fields["task_name"],
        "next_start": next_fields["task_start"],
        "next_project": next_fields["project_name"],
        "next_project_code": next_fields["project_code"],
        "next_user": next_fields["user_name"],
        "running_slot_id": current_slot.id if current_slot else None,
        "running_start": current["task_start"],
    }


def _reconcile_instrument_status(instrument: Instrument, current_slot: TimeSlot | None) -> str:
    if instrument.status in PROTECTED_INSTRUMENT_STATUSES:
        return instrument.status
    effective_status = "running" if current_slot else "idle"
    if instrument.status != effective_status:
        instrument.status = effective_status
    return effective_status


def _next_task_slot(db, instrument_id: int, current_task_id: int | None) -> TimeSlot | None:
    query = db.query(TimeSlot).filter(
        TimeSlot.instrument_id == instrument_id,
        TimeSlot.status == "scheduled",
    )
    if current_task_id:
        query = query.filter(TimeSlot.task_id != current_task_id)
    return query.order_by(TimeSlot.plan_start, TimeSlot.id).first()


def _task_status_fields(db, slot: TimeSlot | None, now: datetime) -> dict:
    if not slot or not slot.task:
        return _empty_task_fields()
    task = slot.task
    project = db.query(Project).filter(Project.id == task.project_id).first()
    task_start, task_end = _task_window(db, task.id)
    progress = None
    if task_start and task_end and task_end > task_start:
        elapsed = (now - task_start).total_seconds()
        total = (task_end - task_start).total_seconds()
        progress = min(max(round(elapsed / total * 100, 1), 0), 100)
    return {
        "task_id": task.id,
        "task_name": task.name,
        "project_name": project.name if project else None,
        "project_code": project.code if project else None,
        "task_start": task_start.isoformat() if task_start else None,
        "task_end": task_end.isoformat() if task_end else None,
        "user_name": task.assignee_name,
        "progress": progress,
    }


def _next_task_fields(db, slot: TimeSlot | None) -> dict:
    if not slot or not slot.task:
        return _empty_next_fields()
    task = slot.task
    project = db.query(Project).filter(Project.id == task.project_id).first()
    task_start, _ = _task_window(db, task.id)
    return {
        "task_name": task.name,
        "task_start": task_start.isoformat() if task_start else None,
        "project_name": project.name if project else None,
        "project_code": project.code if project else None,
        "user_name": task.assignee_name,
    }


def _task_window(db, task_id: int) -> tuple[datetime | None, datetime | None]:
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES | {"completed"}),
    ).all()
    starts = [slot.plan_start for slot in slots if slot.plan_start]
    ends = [slot.plan_end for slot in slots if slot.plan_end]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _empty_task_fields() -> dict:
    return {
        "task_id": None,
        "task_name": None,
        "project_name": None,
        "project_code": None,
        "task_start": None,
        "task_end": None,
        "user_name": None,
        "progress": None,
    }


def _empty_next_fields() -> dict:
    return {
        "task_name": None,
        "task_start": None,
        "project_name": None,
        "project_code": None,
        "user_name": None,
    }
