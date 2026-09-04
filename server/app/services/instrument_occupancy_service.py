from __future__ import annotations

from app.models import Task, TaskExecutionSegment, TimeSlot


ACTIVE_SLOT_STATUSES = {"scheduled", "running", "paused", "blocked", "interrupted"}
COMPLETED_TASK_STATUSES = {"done", "completed"}


def current_occupying_slot(
    db,
    instrument_id: int,
    excluded_task_id: int | None = None,
) -> TimeSlot | None:
    query = (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            TimeSlot.instrument_id == instrument_id,
            TimeSlot.lifecycle_status == "active",
            TimeSlot.actual_start.isnot(None),
            TimeSlot.actual_end.is_(None),
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
            ~Task.status.in_(COMPLETED_TASK_STATUSES),
        )
    )
    if excluded_task_id is not None:
        query = query.filter(Task.id != excluded_task_id)
    return query.order_by(
        TimeSlot.actual_start.desc(), TimeSlot.id.desc()
    ).first()


def current_occupying_task(
    db,
    instrument_id: int,
    excluded_task_id: int | None = None,
) -> Task | None:
    """这台仪器上还没结束的那个任务。

    比"有没有在跑的时间槽"宽一档：任务被按天切成多段时，上一段已按计划边界
    结束、下一段还没到，中间这段空档里没有任何时间槽在跑，但执行流水没有结束，
    人还在这个任务上，仪器也还被它占着。只看时间槽的话，空档期会被当成仪器
    空闲，另一个任务就能在同一台仪器上开起来，于是两个任务同时挂着进行中。

    仪器状态（首页与甘特图）仍然只看时间槽，那边要的是"此刻画面上有没有块"；
    这里要的是"能不能在这台仪器上再开一个任务"，两个问题的答案本就不同。
    """
    slot = current_occupying_slot(db, instrument_id, excluded_task_id)
    if slot is not None:
        return slot.task
    return _running_task_between_slots(db, instrument_id, excluded_task_id)


def _running_task_between_slots(
    db,
    instrument_id: int,
    excluded_task_id: int | None,
) -> Task | None:
    query = (
        db.query(Task)
        .join(TaskExecutionSegment, TaskExecutionSegment.task_id == Task.id)
        .filter(
            TaskExecutionSegment.ended_at.is_(None),
            TaskExecutionSegment.instrument_id == instrument_id,
            Task.status == "running",
        )
    )
    if excluded_task_id is not None:
        query = query.filter(Task.id != excluded_task_id)
    return query.order_by(
        TaskExecutionSegment.started_at.desc(), TaskExecutionSegment.id.desc()
    ).first()
