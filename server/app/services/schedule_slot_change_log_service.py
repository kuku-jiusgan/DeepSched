from __future__ import annotations

from app.models import ScheduleSlotChangeLog, Task, TimeSlot


def _project_id(db, slot: TimeSlot) -> int | None:
    if slot.task is not None:
        return slot.task.project_id
    task = db.get(Task, slot.task_id)
    return task.project_id if task else None


def record_slot_created(db, slot: TimeSlot, reason_type: str = "replan") -> None:
    db.add(ScheduleSlotChangeLog(
        schedule_run_id=slot.schedule_run_id,
        slot_id=slot.id,
        task_id=slot.task_id,
        project_id=_project_id(db, slot),
        instrument_id=slot.instrument_id,
        change_type="created",
        reason_type=reason_type,
        after_start=slot.plan_start,
        after_end=slot.plan_end,
        after_status=slot.status,
    ))


def record_slot_deleted(db, slot: TimeSlot, reason_type: str = "replan") -> None:
    db.add(ScheduleSlotChangeLog(
        schedule_run_id=slot.schedule_run_id,
        slot_id=slot.id,
        task_id=slot.task_id,
        project_id=_project_id(db, slot),
        instrument_id=slot.instrument_id,
        change_type="deleted",
        reason_type=reason_type,
        before_start=slot.plan_start,
        before_end=slot.plan_end,
        before_status=slot.status,
    ))
