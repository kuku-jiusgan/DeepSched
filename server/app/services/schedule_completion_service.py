from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AuditLog, Task, TaskExecutionSegment, TimeSlot
from app.services.instrument_status_service import refresh_instrument_status
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations
from app.services.schedule_advance_notification_service import notify_advanced_task_assignees
from app.services.schedule_early_completion_replan_service import (
    replan_released_resource_queue,
)
from app.services.schedule_queue_replan_support import (
    load_working_options as _load_working_options,
)
from app.services.project_status_service import calculate_project_status
from app.services.task_delay_status_service import mark_task_delayed
from app.services.schedule_delay_propagation_service import propagate_actual_delay
from app.services.schedule_delay_service import ScheduleDelayInvalidError
from app.services.task_execution_service import TaskExecutionInvalidError, start_task_execution
from app.services.task_progress_service import planned_task_minutes

def complete_task_and_shift(
    db: Session,
    task_id: int,
    actual_end_time: datetime | None = None,
    completed_slot_id: int | None = None,
    release_instrument: bool = True,
) -> dict:
    end_time = actual_end_time or datetime.now()
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return {"status": "error", "message": "未找到指定任务"}
    if task.status in {"completed", "done"}:
        return {"status": "error", "message": "任务已经完成，不能重复执行完成操作"}

    task_slots = (
        db.query(TimeSlot)
        .filter(TimeSlot.task_id == task_id, TimeSlot.lifecycle_status == "active")
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    if not task_slots:
        return {"status": "error", "message": "任务没有排程时段"}

    planned_end = max(slot.plan_end for slot in task_slots)
    task.status = "completed"
    _close_running_execution_segment(db, task.id, end_time)
    if end_time > planned_end:
        mark_task_delayed(task)
    completed_slot = _select_completed_slot(task_slots, completed_slot_id, end_time)
    affected_instrument_ids = {slot.instrument_id for slot in task_slots if slot.instrument_id}
    _mark_task_slots_completed(db, task_slots, completed_slot, end_time)
    # Completion can supersede future manual slots. Keep the derived bridge
    # occupancy synchronized before any released-resource replan is evaluated.
    rebuild_instrument_bridge_reservations(db)
    delay_result = _propagate_delay_safely(db, task, planned_end, end_time)
    if task.project:
        task.project.status = calculate_project_status(task.project)

    for instrument_id in affected_instrument_ids:
        refresh_instrument_status(db, instrument_id)

    db.flush()
    if not release_instrument:
        return {
            "status": "ok",
            "message": "任务已完成，未释放仪器，后续排程保持不变",
            "moved_tasks": 0,
            "released_instrument": False,
            "delayed_slots": delay_result["shifted_slots"],
            "delay_affected_tasks": delay_result["affected_tasks"],
        }
    resumed_task, resume_warning = _resume_paused_source_task(db, task.id)
    if resumed_task:
        db.flush()
        resumed_end = _active_task_plan_end(db, resumed_task.id)
        result = _forward_shift_instrument_queue(
            db, completed_slot.instrument_id, resumed_end, resumed_task.assignee_id,
            resumed_task.project_id,
        )
        moved_task_details = result.pop("moved_task_details", [])
        notify_advanced_task_assignees(db, task, end_time, planned_end, moved_task_details)
        delay_warning = delay_result.get("warning")
        delayed_message = delay_warning or (
            f"任务已延期完成，已顺延 {delay_result['affected_tasks']} 个受影响任务"
            if end_time > planned_end else ""
        )
        return {
            "status": "ok",
            "message": "；".join(filter(None, [
                delayed_message,
                f"任务已完成，已恢复原暂停任务【{resumed_task.name}】",
                result["message"],
            ])),
            "moved_tasks": result["moved_tasks"],
            "released_instrument": True,
            "resumed_task_id": resumed_task.id,
            "resumed_task_name": resumed_task.name,
            "delayed_slots": delay_result["shifted_slots"],
            "delay_affected_tasks": delay_result["affected_tasks"],
        }
    # Early completion is a released-resource event, not a project-wide replan.
    # The resource closure preserves dependencies without pulling unrelated
    # top-level branches (and their independent approval gates) into the solve.
    result = _forward_shift_instrument_queue(
        db, completed_slot.instrument_id, end_time, task.assignee_id, task.project_id,
    )
    moved_task_details = result.pop("moved_task_details", [])
    notify_advanced_task_assignees(db, task, end_time, planned_end, moved_task_details)
    db.flush()
    delay_warning = delay_result.get("warning")
    result["message"] = "；".join(filter(None, [
        delay_warning,
        resume_warning,
        result["message"],
    ]))
    result["released_instrument"] = True
    result["delayed_slots"] = delay_result["shifted_slots"]
    result["delay_affected_tasks"] = delay_result["affected_tasks"]
    return result


def _resume_paused_source_task(
    db: Session,
    completed_task_id: int,
) -> tuple[Task | None, str | None]:
    for log in _recent_pause_switch_logs(db):
        detail = log.detail if isinstance(log.detail, dict) else {}
        if detail.get("target_task_id") != completed_task_id:
            continue
        source_task = db.query(Task).filter(Task.id == detail.get("source_task_id", log.target_id)).first()
        source_slot_id = detail.get("source_slot_id")
        if not source_task or source_task.status != "paused" or not source_slot_id:
            continue
        resumable_slot = next(
            (
                slot for slot in sorted(source_task.time_slots, key=lambda item: (item.plan_start, item.id))
                if slot.status == "paused" and slot.actual_start is None
            ),
            None,
        )
        source_slot_id = resumable_slot.id if resumable_slot else source_slot_id
        # 暂停切换链中的源任务由当前任务完成后恢复，不应再次被链上
        # 尚未完成的暂停任务拦截；普通手动启动仍保留前序校验。
        try:
            start_task_execution(
                db,
                int(source_slot_id),
                allow_queue_insert=True,
                advance_schedule=True,
            )
        except TaskExecutionInvalidError as exc:
            return None, (
                f"原暂停任务【{source_task.name}】未恢复：{exc}，请重新排程后再启动"
            )
        return source_task, None
    return None, None


def _recent_pause_switch_logs(db: Session) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(AuditLog.action == "task_paused")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(100)
        .all()
    )


def _close_running_execution_segment(db, task_id: int, ended_at: datetime) -> None:
    segment = (
        db.query(TaskExecutionSegment)
        .filter(
            TaskExecutionSegment.task_id == task_id,
            TaskExecutionSegment.ended_at.is_(None),
        )
        .order_by(TaskExecutionSegment.started_at.desc(), TaskExecutionSegment.id.desc())
        .first()
    )
    if segment:
        segment.ended_at = ended_at
        segment.end_reason = "completed"


def _propagate_delay_safely(
    db: Session,
    task: Task,
    planned_end: datetime,
    actual_end: datetime,
) -> dict:
    try:
        with db.begin_nested():
            return propagate_actual_delay(db, task, planned_end, actual_end)
    except ScheduleDelayInvalidError as exc:
        return {
            "shifted_slots": 0,
            "affected_tasks": 0,
            "warning": f"任务已延期完成，但后续任务无法自动顺延：{exc}",
        }


def _select_completed_slot(
    slots: list[TimeSlot],
    completed_slot_id: int | None,
    end_time: datetime,
) -> TimeSlot:
    running_slot = next(
        (
            slot for slot in slots
            if slot.actual_start is not None and slot.actual_end is None
        ),
        None,
    )
    if running_slot:
        return running_slot
    active_slot = next(
        (slot for slot in slots if slot.plan_start <= end_time <= slot.plan_end),
        None,
    )
    if active_slot:
        return active_slot

    started_slots = [slot for slot in slots if slot.plan_start <= end_time]
    if started_slots:
        return started_slots[-1]

    if completed_slot_id is not None:
        matched = next((slot for slot in slots if slot.id == completed_slot_id), None)
        if matched:
            return matched
    return slots[0]


def _mark_task_slots_completed(
    db: Session,
    slots: list[TimeSlot],
    completed_slot: TimeSlot,
    end_time: datetime,
) -> None:
    for slot in slots:
        if slot.id != completed_slot.id and slot.plan_start > end_time:
            slot.lifecycle_status = "superseded"
            slot.status = "cancelled"
            slot.superseded_at = end_time
            slot.superseded_reason = "任务提前完成"
            continue
        slot.status = "completed"
        if slot.actual_start is None:
            slot.actual_start = slot.plan_start
        slot.actual_end = end_time if slot.id == completed_slot.id else min(slot.plan_end, end_time)


def _forward_shift_instrument_queue(
    db: Session,
    instrument_id: int | None,
    released_at: datetime,
    assignee_id: int | None = None,
    previous_project_id: int | None = None,
) -> dict:
    return replan_released_resource_queue(
        db,
        instrument_id,
        released_at,
        assignee_id,
        previous_project_id,
    )


def _active_task_plan_end(db: Session, task_id: int) -> datetime:
    plan_end = db.query(func.max(TimeSlot.plan_end)).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
    ).scalar()
    if plan_end is None:
        raise ScheduleDelayInvalidError("恢复任务没有活动计划时段，无法重排后续队列")
    return plan_end
