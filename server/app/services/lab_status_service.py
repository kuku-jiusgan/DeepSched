from __future__ import annotations

from datetime import datetime

from app.models import Instrument, Project, Task, TimeSlot
from app.services.instrument_occupancy_service import ACTIVE_SLOT_STATUSES
from app.services.instrument_status_service import (
    effective_instrument_status, running_instrument_tasks,
)


COMPLETED_TASK_STATUSES = {"done", "completed"}


def list_lab_status(db) -> list[dict]:
    instruments = db.query(Instrument).filter(Instrument.availability_status == "available").all()
    now = datetime.now()
    running_slots = _current_slots_by_instrument(db)
    # 展示口径与仪器甘特图共用一份（instrument_status_service.instrument_is_working）：
    # 只要这台仪器上还有仪器任务处于进行中就算运作中，哪怕此刻没有正在跑的时间槽。
    display_slots = _display_slots(db, instruments, running_slots)
    status_data = _load_status_data(db, instruments, display_slots)
    items = [
        _instrument_status(
            db, instrument, now,
            display_slots.get(instrument.id), running_slots.get(instrument.id),
            status_data,
        )
        for instrument in instruments
    ]
    if db.dirty:
        db.commit()
    return items


def _display_slots(db, instruments, running_slots: dict[int, TimeSlot]) -> dict[int, TimeSlot]:
    """每台仪器"当前任务"那一栏该显示哪一段。

    有正在跑的时间槽就用它；没有、但仪器上有任务处于进行中时，用那个任务下一段还
    没做完的时间槽——否则界面会出现"运作中，当前任务却是空的"这种自相矛盾。
    """
    display = dict(running_slots)
    pending_ids = [item.id for item in instruments if item.id not in display]
    for instrument_id, task in running_instrument_tasks(db, pending_ids).items():
        slot = next(
            (
                item for item in sorted(task.time_slots, key=lambda s: (s.plan_start, s.id))
                if item.instrument_id == instrument_id
                and item.lifecycle_status == "active"
                and item.actual_end is None
            ),
            None,
        )
        if slot is not None:
            display[instrument_id] = slot
    return display


def _instrument_status(
    db,
    instrument: Instrument,
    now: datetime,
    display_slot: TimeSlot | None,
    running_slot: TimeSlot | None,
    status_data,
) -> dict:
    status = _reconcile_instrument_status(db, instrument)
    current = _task_status_fields(display_slot, now, status_data)
    upcoming = status_data["next_slots"].get(instrument.id)
    next_fields = _next_task_fields(upcoming, status_data)
    return {
        "id": instrument.id,
        "code": instrument.code,
        "name": instrument.name,
        "group": instrument.instrument_group,
        "location": instrument.location,
        "status": status,
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
        # 这两个字段说的是"此刻真的在跑的那一段"，不跟着展示口径放宽：放宽的是
        # 状态标签，不是"可以对它做操作的那个时间槽"。
        "running_slot_id": running_slot.id if running_slot else None,
        "running_start": current["task_start"],
    }


def _load_status_data(db, instruments, current_slots):
    instrument_ids = [instrument.id for instrument in instruments]
    slots = db.query(TimeSlot).filter(
        TimeSlot.instrument_id.in_(instrument_ids),
        TimeSlot.lifecycle_status == "active",
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


def _reconcile_instrument_status(db, instrument: Instrument) -> str:
    """状态判据只有一处：effective_instrument_status。

    首页和仪器甘特图展示的是同一份事实，判据分成两份写、哪怕当下结论一样，也早晚
    会有一边被改动而另一边没跟上——用户看到的就是系统自相矛盾。这里只负责把结论
    回写到仪器行上。
    """
    effective_status = effective_instrument_status(db, instrument)
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
