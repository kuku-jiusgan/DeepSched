from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AuditLog, Task, TaskExecutionSegment, TimeSlot
from app.services.instrument_status_service import refresh_instrument_status
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations
from app.services.schedule_advance_notification_service import notify_advanced_task_assignees
from app.services.schedule_forward_slot_service import build_forward_slots
from app.services.schedule_slot_change_log_service import record_slot_created, supersede_slot
from app.services.schedule_queue_replan_support import (
    cross_project_setup_minutes,
    dependency_ready_time,
    is_movable_task,
    load_forward_shift_candidates as _load_forward_shift_candidates,
    load_working_options as _load_working_options,
    replan_duration_minutes,
    tier_for_start,
)
from app.services.project_status_service import calculate_project_status
from app.services.task_delay_status_service import mark_task_delayed
from app.services.schedule_delay_propagation_service import propagate_actual_delay
from app.services.schedule_delay_service import ScheduleDelayInvalidError
from app.services.task_execution_service import TaskExecutionInvalidError, start_task_execution
from app.services.task_progress_service import planned_task_minutes
from app.schemas.schemas import RescheduleRequest

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
    result = _replan_dependency_projects_after_completion(
        db, completed_slot.instrument_id, end_time, task.assignee_id,
    )
    if result is None:
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


def _replan_dependency_projects_after_completion(
    db: Session,
    instrument_id: int | None,
    released_at: datetime,
    assignee_id: int | None,
) -> dict | None:
    """Use the project solver when released-resource candidates have dependencies."""
    candidates = _load_forward_shift_candidates(db, instrument_id, released_at, assignee_id)
    dependency_projects = {
        task.project_id
        for task in candidates
        if task.predecessors
    }
    if not dependency_projects:
        return None

    from app.services.schedule_reschedule_service import _project_reschedule

    moved = 0
    details: list[dict] = []
    for project_id in sorted(dependency_projects):
        task = next(item for item in candidates if item.project_id == project_id)
        result = _project_reschedule(
            db,
            RescheduleRequest(
                trigger_type="early_completion",
                strategy="project",
                affected_task_id=task.id,
            ),
        )
        if result.get("status") != "ok":
            return {
                "status": "error",
                "message": result.get("message") or "项目重排失败",
                "moved_tasks": moved,
                "moved_task_details": details,
            }
        moved += int(result.get("moved_tasks", 0) or 0)
    return {
        "status": "ok",
        "message": f"任务已完成，已按项目依赖重排 {moved} 个任务",
        "moved_tasks": moved,
        "moved_task_details": details,
    }


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
    candidate_tasks = _load_forward_shift_candidates(db, instrument_id, released_at, assignee_id)
    if not candidate_tasks:
        return {
            "status": "ok",
            "message": "任务已完成，无后续任务可前移" if instrument_id is None else "任务已完成，该仪器无后续任务可前移",
            "moved_tasks": 0,
        }

    working_options = _load_working_options(db, released_at)
    original_slots = {
        task.id: (
            db.query(TimeSlot)
            .filter(
                TimeSlot.task_id == task.id,
                TimeSlot.status == "scheduled",
                TimeSlot.actual_start.is_(None),
                TimeSlot.lifecycle_status == "active",
            )
            .order_by(TimeSlot.plan_start)
            .all()
        )
        for task in candidate_tasks
    }
    slot_snapshots = {
        task_id: [_snapshot_slot(slot) for slot in slots]
        for task_id, slots in original_slots.items()
    }
    movable_tasks = {
        task.id: is_movable_task(db, task, instrument_id, released_at, assignee_id)
        for task in candidate_tasks
    }
    movable_prefix = []
    for task in candidate_tasks:
        if not movable_tasks[task.id]:
            break
        movable_prefix.append(task)
    candidate_tasks = movable_prefix
    original_slots = {task.id: original_slots[task.id] for task in candidate_tasks}
    slot_snapshots = {task_id: slot_snapshots[task_id] for task_id in original_slots}

    for slots in original_slots.values():
        for slot in slots:
            slot.lifecycle_status = "superseded"
    db.flush()

    moved = 0
    moved_task_details = []
    cursors: dict[int | None, datetime] = {}
    project_cursors = {instrument_id: previous_project_id}
    setup_minutes = cross_project_setup_minutes(db)
    for task in candidate_tasks:
        snapshots = slot_snapshots[task.id]
        if not snapshots:
            continue

        original_start = snapshots[0]["plan_start"]
        original_end = snapshots[-1]["plan_end"]
        duration_minutes = replan_duration_minutes(task, original_slots[task.id])
        slot_instrument_id = snapshots[0]["instrument_id"]
        slot_cursor = cursors.get(slot_instrument_id, released_at)
        prior_project_id = project_cursors.get(slot_instrument_id)
        if (
            slot_instrument_id is not None
            and prior_project_id is not None
            and prior_project_id != task.project_id
        ):
            slot_cursor += timedelta(minutes=setup_minutes)
        earliest_start = max(
            slot_cursor,
            dependency_ready_time(db, task, released_at),
            task.earliest_start or released_at,
            task.project.start_date if task.project and task.project.start_date else released_at,
        )
        new_slots = build_forward_slots(
            db,
            task,
            slot_instrument_id,
            duration_minutes,
            earliest_start,
            working_options,
        )

        dependency_ready = dependency_ready_time(db, task, released_at)
        if new_slots and new_slots[0][0] < dependency_ready:
            new_slots = []

        if not new_slots or new_slots[0][0] >= original_start:
            _restore_active_slots(original_slots[task.id])
            cursors[slot_instrument_id] = max(slot_cursor, original_end)
            project_cursors[slot_instrument_id] = task.project_id
            continue

        generated_minutes = sum(
            int((end - start).total_seconds() / 60)
            for start, end in new_slots
        )
        if generated_minutes != duration_minutes:
            raise ScheduleDelayInvalidError(
                f"任务【{task.name}】重排工时不守恒：应为 {duration_minutes} 分钟，"
                f"实际生成 {generated_minutes} 分钟"
            )

        created_slots = []
        for start, end in new_slots:
            new_slot = TimeSlot(
                task_id=task.id,
                schedule_run_id=snapshots[0].get("schedule_run_id", "legacy"),
                instrument_id=slot_instrument_id,
                plan_start=start,
                plan_end=end,
                tier=tier_for_start(db, start),
                status="scheduled",
            )
            db.add(new_slot)
            created_slots.append(new_slot)
        db.flush()
        for old_slot in original_slots[task.id]:
            old_slot.lifecycle_status = "active"
            supersede_slot(db, old_slot, "任务提前完成后局部重排")
        for new_slot in created_slots:
            record_slot_created(db, new_slot, "early_completion_replan")
        cursors[slot_instrument_id] = new_slots[-1][1]
        project_cursors[slot_instrument_id] = task.project_id
        moved += 1
        moved_task_details.append({
            "task_id": task.id,
            "original_start": original_start,
            "original_end": original_end,
            "new_start": new_slots[0][0],
            "new_end": new_slots[-1][1],
        })

    db.flush()
    rebuild_instrument_bridge_reservations(db)
    return {
        "status": "ok",
        "message": (
            f"任务已完成，按责任人前移 {moved} 个任务"
            if instrument_id is None
            else f"任务已完成，该仪器跨项目前移 {moved} 个任务"
        ),
        "moved_tasks": moved,
        "moved_task_details": moved_task_details,
    }


def _snapshot_slot(slot: TimeSlot) -> dict:
    return {
        "task_id": slot.task_id,
        "schedule_run_id": slot.schedule_run_id,
        "instrument_id": slot.instrument_id,
        "plan_start": slot.plan_start,
        "plan_end": slot.plan_end,
        "tier": slot.tier,
        "status": slot.status,
    }


def _restore_active_slots(slots: list[TimeSlot]) -> None:
    for slot in slots:
        slot.lifecycle_status = "active"


def _active_task_plan_end(db: Session, task_id: int) -> datetime:
    plan_end = db.query(func.max(TimeSlot.plan_end)).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
    ).scalar()
    if plan_end is None:
        raise ScheduleDelayInvalidError("恢复任务没有活动计划时段，无法重排后续队列")
    return plan_end
