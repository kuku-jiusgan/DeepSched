from __future__ import annotations

from datetime import datetime

from app.models import Task, TaskExecutionSegment, TimeSlot
from app.services.instrument_status_service import mark_instrument_running
from app.services.instrument_occupancy_service import current_occupying_slot
from app.services.task_delay_status_service import mark_task_delayed
from app.domain.errors import DomainConflictError, DomainNotFoundError


COMPLETED_TASK_STATUSES = {"done", "completed"}
STARTABLE_SLOT_STATUSES = {"scheduled", "blocked", "paused", "interrupted"}
RUNNING_CONTINUATION_STATUSES = {"scheduled", "running", "blocked", "paused", "interrupted"}


class TaskExecutionNotFoundError(DomainNotFoundError):
    pass


class TaskExecutionInvalidError(DomainConflictError):
    pass


def start_task_execution(db, slot_id: int, operator_id: int | None = None) -> dict[str, str]:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise TaskExecutionNotFoundError("时间槽不存在")
    task = db.query(Task).filter(Task.id == slot.task_id).first()
    if not task:
        raise TaskExecutionNotFoundError("任务不存在")
    _ensure_can_start(db, task, slot)

    started_at = datetime.now()
    task.status = "running"
    if slot.plan_start and started_at > slot.plan_start:
        mark_task_delayed(task)
    if task.project:
        task.project.status = "active"
    for running_slot in _continuous_slots(db, slot):
        running_slot.status = "running"
        if running_slot.id == slot.id:
            running_slot.actual_start = started_at
            running_slot.actual_end = None
        mark_instrument_running(db, running_slot.instrument_id)
    db.add(TaskExecutionSegment(
        task_id=task.id,
        slot_id=slot.id,
        instrument_id=slot.instrument_id,
        operator_id=operator_id,
        started_at=started_at,
    ))
    return {"status": "ok"}


def ensure_predecessors_completed(task: Task) -> None:
    incomplete = []
    for dependency in task.predecessors:
        for name in _incomplete_leaf_task_names(dependency.predecessor):
            if name not in incomplete:
                incomplete.append(name)
    if incomplete:
        names = "、".join(incomplete[:3])
        raise TaskExecutionInvalidError(f"前置任务【{names}】尚未完成，不能操作【{task.name}】")


def predecessors_completed(task: Task) -> bool:
    return all(
        not _incomplete_leaf_task_names(dependency.predecessor)
        for dependency in task.predecessors
    )


def _incomplete_leaf_task_names(task: Task) -> list[str]:
    if task.children:
        return [
            name
            for child in task.children
            for name in _incomplete_leaf_task_names(child)
        ]
    return [] if task.status in COMPLETED_TASK_STATUSES else [task.name]


def _ensure_can_start(db, task: Task, slot: TimeSlot) -> None:
    if task.status in COMPLETED_TASK_STATUSES or slot.status == "completed":
        raise TaskExecutionInvalidError("任务已经完成，不能重复开始")
    if task.status == "running" or any(
        task_slot.actual_start is not None and task_slot.actual_end is None
        for task_slot in task.time_slots
    ):
        raise TaskExecutionInvalidError("任务已经开始，不能重复操作")
    if slot.status not in STARTABLE_SLOT_STATUSES:
        raise TaskExecutionInvalidError("当前任务状态不能开始")
    ensure_predecessors_completed(task)
    if not task.requires_instrument:
        return
    if not slot.instrument_id:
        raise TaskExecutionInvalidError("仪器任务尚未分配仪器，不能启动")
    occupying_slot = current_occupying_slot(
        db,
        slot.instrument_id,
        excluded_task_id=task.id,
    )
    if occupying_slot and occupying_slot.task:
        raise TaskExecutionInvalidError(
            f"仪器当前任务【{occupying_slot.task.name}】尚未结束，不能启动【{task.name}】"
        )


def _continuous_slots(db, start_slot: TimeSlot) -> list[TimeSlot]:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == start_slot.task_id,
            TimeSlot.plan_end >= start_slot.plan_start,
            TimeSlot.status.in_(RUNNING_CONTINUATION_STATUSES),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
