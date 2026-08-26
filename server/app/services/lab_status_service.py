from __future__ import annotations

from datetime import datetime

from app.models import Instrument, Project, Task, TimeSlot
from app.services.instrument_occupancy_service import ACTIVE_SLOT_STATUSES


PROTECTED_INSTRUMENT_STATUSES = {"fault", "maintenance"}
COMPLETED_TASK_STATUSES = {"done", "completed"}


def list_lab_status(db) -> list[dict]:
    instruments = db.query(Instrument).filter(Instrument.availability_status == "available").all()
    now = datetime.now()
    current_slots = _current_slots_by_instrument(db)
    status_data = _load_status_data(db, instruments, current_slots)
    items = [
        _instrument_status(instrument, now, current_slots.get(instrument.id), status_data)
        for instrument in instruments
    ]
    if db.dirty:
        db.commit()
    return items


def _instrument_status(instrument: Instrument, now: datetime, current_slot: TimeSlot | None, status_data) -> dict:
    status = _reconcile_instrument_status(instrument, current_slot)
    current = _task_status_fields(current_slot, now, status_data)
    upcoming = status_data["next_slots"].get(instrument.id)
    next_fields = _next_task_fields(upcoming, status_data)
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


def _load_status_data(db, instruments, current_slots):
    instrument_ids = [instrument.id for instrument in instruments]
    slots = db.query(TimeSlot).filter(
        TimeSlot.instrument_id.in_(instrument_ids),
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES | {"completed", "scheduled"}),
    ).all() if instrument_ids else []
    next_slots = {}
    for slot in sorted((slot for slot in slots if slot.status == "scheduled"), key=lambda item: (item.plan_start, item.id)):
        current = current_slots.get(slot.instrument_id)
        if current and current.task_id == slot.task_id:
            continue
        next_slots.setdefault(slot.instrument_id, slot)
    task_ids = {slot.task_id for slot in slots}
    task_windows = {}
    for slot in slots:
        start, end = task_windows.get(slot.task_id, (None, None))
        task_windows[slot.task_id] = (min(value for value in (start, slot.plan_start) if value), max(value for value in (end, slot.plan_end) if value))
    project_ids = {slot.task.project_id for slot in slots if slot.task is not None}
    projects = db.query(Project).filter(Project.id.in_(project_ids)).all() if project_ids else []
    return {
        "next_slots": next_slots,
        "task_windows": task_windows,
        "projects": {project.id: project for project in projects},
    }


def _current_slots_by_instrument(db) -> dict[int, TimeSlot]:
    rows = (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            TimeSlot.instrument_id.isnot(None),
            TimeSlot.lifecycle_status == "active",
            TimeSlot.actual_start.isnot(None),
            TimeSlot.actual_end.is_(None),
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
            ~Task.status.in_(COMPLETED_TASK_STATUSES),
        )
        .order_by(TimeSlot.actual_start.desc(), TimeSlot.id.desc())
        .all()
    )
    current: dict[int, TimeSlot] = {}
    for slot in rows:
        if slot.instrument_id not in current:
            current[slot.instrument_id] = slot
    return current


def _reconcile_instrument_status(instrument: Instrument, current_slot: TimeSlot | None) -> str:
    if instrument.status in PROTECTED_INSTRUMENT_STATUSES:
        return instrument.status
    effective_status = "running" if current_slot else "idle"
    if instrument.status != effective_status:
        instrument.status = effective_status
    return effective_status


def _task_status_fields(slot: TimeSlot | None, now: datetime, status_data) -> dict:
    if not slot or not slot.task:
        return _empty_task_fields()
    task = slot.task
    project = status_data["projects"].get(task.project_id)
    task_start, task_end = status_data["task_windows"].get(task.id, (None, None))
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


def _next_task_fields(slot: TimeSlot | None, status_data) -> dict:
    if not slot or not slot.task:
        return _empty_next_fields()
    task = slot.task
    project = status_data["projects"].get(task.project_id)
    task_start, _ = status_data["task_windows"].get(task.id, (None, None))
    return {
        "task_name": task.name,
        "task_start": task_start.isoformat() if task_start else None,
        "project_name": project.name if project else None,
        "project_code": project.code if project else None,
        "user_name": task.assignee_name,
    }


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
