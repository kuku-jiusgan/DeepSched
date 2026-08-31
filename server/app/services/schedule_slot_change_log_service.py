from __future__ import annotations

from app.models import ScheduleSlotChangeLog, Task, TimeSlot
from app.services.instrument_bridge_sync_service import invalidate_task_bridge_reservations
from datetime import datetime


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

def supersede_slot(db, slot: TimeSlot, reason: str, replacement: TimeSlot | None = None, operator_id: int | None = None) -> None:
    if slot.actual_start is not None or slot.actual_end is not None:
        raise ValueError("已发生时间槽不可被替代")
    slot.lifecycle_status = "superseded"
    # 作废的同时必须把执行状态一起收掉。只改生命周期的话，一个当时状态为
    # running 的槽会带着这个状态永远留在库里，任何忘记过滤生命周期的查询都
    # 会把它当成"仪器正在运行"。项目里另外三处作废逻辑都设了这一行。
    slot.status = "cancelled"
    slot.superseded_at = datetime.now()
    slot.superseded_reason = reason
    slot.superseded_by = operator_id
    if replacement is not None:
        slot.superseded_by_slot_id = replacement.id
    invalidate_task_bridge_reservations(db, slot.task_id)
    db.add(ScheduleSlotChangeLog(
        schedule_run_id=slot.schedule_run_id, task_id=slot.task_id,
        project_id=_project_id(db, slot), instrument_id=slot.instrument_id,
        slot_id=slot.id, change_type="superseded", reason_type=reason,
        before_start=slot.plan_start, before_end=slot.plan_end,
        before_status=slot.status, operator_id=operator_id,
    ))
