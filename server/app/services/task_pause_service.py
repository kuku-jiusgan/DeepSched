from __future__ import annotations

from datetime import datetime

from app.domain.errors import DomainConflictError, DomainNotFoundError, DomainValidationError
from app.models import Task, TaskExecutionSegment, TimeSlot
from app.services.audit_log_service import record_audit_log
from app.services.instrument_status_service import refresh_instrument_status
from app.services.task_execution_service import predecessors_completed, start_task_execution


CANDIDATE_TASK_STATUSES = {"scheduled", "paused"}
CANDIDATE_SLOT_STATUSES = {"scheduled", "paused", "interrupted"}


def list_switch_candidates(db, source_slot_id: int) -> list[dict]:
    source_slot, source_task = _running_source(db, source_slot_id)
    if not source_slot.instrument_id:
        return []

    slots = (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            TimeSlot.instrument_id == source_slot.instrument_id,
            TimeSlot.task_id != source_task.id,
            TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
            Task.status.in_(CANDIDATE_TASK_STATUSES),
            Task.requires_instrument.is_(True),
            ~Task.children.any(),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    candidates = []
    seen_task_ids: set[int] = set()
    for slot in slots:
        task = slot.task
        if task.id in seen_task_ids or not predecessors_completed(task):
            continue
        seen_task_ids.add(task.id)
        candidates.append(_candidate_out(slot, task))
    return candidates


def pause_and_switch_task(
    db,
    source_slot_id: int,
    reason: str,
    operator,
    target_slot_id: int | None = None,
) -> dict[str, str]:
    clean_reason = reason.strip()
    if not clean_reason:
        raise DomainValidationError("请填写暂停原因")
    source_slot, source_task = _running_source(db, source_slot_id)
    target_slot = _validated_target(db, source_slot, target_slot_id) if target_slot_id else None

    paused_at = datetime.now()
    source_slot.status = "paused"
    source_slot.actual_end = paused_at
    source_task.status = "paused"
    _close_execution_segment(db, source_slot, paused_at, clean_reason, operator.id)
    db.flush()
    refresh_instrument_status(db, source_slot.instrument_id)

    target_task_name = None
    if target_slot:
        target_task_name = target_slot.task.name
        start_task_execution(db, target_slot.id, operator.id)

    record_audit_log(
        db,
        operator.display_name or operator.username,
        "task_paused",
        "task",
        source_task.id,
        {
            "reason": clean_reason,
            "instrument_id": source_slot.instrument_id,
            "target_task_id": target_slot.task_id if target_slot else None,
            "target_slot_id": target_slot.id if target_slot else None,
        },
    )
    message = "任务已暂停，仪器已释放"
    if target_task_name:
        message = f"任务已暂停，已切换至【{target_task_name}】"
    return {"status": "ok", "message": message}


def _running_source(db, slot_id: int) -> tuple[TimeSlot, Task]:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise DomainNotFoundError("时间槽不存在")
    task = slot.task
    if not task:
        raise DomainNotFoundError("任务不存在")
    if task.status != "running" or slot.actual_start is None or slot.actual_end is not None:
        raise DomainConflictError("只有正在运行且占用仪器的任务可以暂停")
    return slot, task


def _validated_target(db, source_slot: TimeSlot, target_slot_id: int) -> TimeSlot:
    candidates = list_switch_candidates(db, source_slot.id)
    candidate_ids = {candidate["slot_id"] for candidate in candidates}
    if target_slot_id not in candidate_ids:
        raise DomainConflictError("接替任务不满足仪器、状态或前置任务条件")
    return db.query(TimeSlot).filter(TimeSlot.id == target_slot_id).first()


def _close_execution_segment(
    db,
    slot: TimeSlot,
    ended_at: datetime,
    reason: str,
    operator_id: int,
) -> None:
    segment = (
        db.query(TaskExecutionSegment)
        .filter(
            TaskExecutionSegment.task_id == slot.task_id,
            TaskExecutionSegment.ended_at.is_(None),
        )
        .order_by(TaskExecutionSegment.started_at.desc(), TaskExecutionSegment.id.desc())
        .first()
    )
    if segment:
        segment.ended_at = ended_at
        segment.end_reason = "paused"
        segment.pause_reason = reason
        return
    db.add(TaskExecutionSegment(
        task_id=slot.task_id,
        slot_id=slot.id,
        instrument_id=slot.instrument_id,
        operator_id=operator_id,
        started_at=slot.actual_start,
        ended_at=ended_at,
        end_reason="paused",
        pause_reason=reason,
    ))


def _candidate_out(slot: TimeSlot, task: Task) -> dict:
    project = task.project
    return {
        "slot_id": slot.id,
        "task_id": task.id,
        "task_name": task.name,
        "project_code": project.code if project else "-",
        "project_name": project.name if project else "-",
        "assignee_name": task.assignee.display_name if task.assignee else None,
        "plan_start": slot.plan_start,
        "plan_end": slot.plan_end,
        "is_paused": task.status == "paused",
    }
