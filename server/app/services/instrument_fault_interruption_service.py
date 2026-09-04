"""仪器故障时中断这台仪器上正在进行的任务。

仪器故障相当于一个最高优先级的插单：它当场打断这台仪器上正在做的活，把当前
任务截断，剩下的部分排到维修之后，等仪器修好了再由人接着做。所以处理方式与
暂停切换一致——任务置为暂停，而不是继续挂着"进行中"。

如果只把后面的时间槽挪到维修之后、任务却仍是"进行中"，就会留下一个没有任何
在跑时间槽的幽灵任务：仪器上后来开始的另一个任务与它同时显示为运行中，执行
流水一直不结束，项目实际工时和仪器利用率按"一直做到现在"累加，后续重排还会
因为它没有可锚定的时间槽而把剩余工时整个丢掉。

被打断的那个时间槽按排程颗粒度截断，没做完的部分留成一个新的待排时间槽，交给
故障重排一起挪到维修之后，不能凭空消失；维修实际完成得早或晚，由故障关闭时的
重排按真实完成时间再调一次，随后把任务自动恢复为进行中——点"维修完成"的人就在
仪器旁边，不该再让他到工作台上多点一次继续。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models import Task, TaskExecutionSegment, TimeSlot
from app.services.schedule_working_time_service import working_hours_between
from app.services.scheduler_helpers import TIME_UNIT_MINUTES
from app.services.task_execution_service import (
    TaskExecutionInvalidError,
    start_task_execution,
)
from app.services.task_progress_service import planned_task_minutes


INTERRUPT_END_REASON = "instrument_fault"

_logger = logging.getLogger(__name__)


def interrupt_running_tasks_on_instrument(
    db,
    instrument_id: int,
    interrupted_at: datetime,
    reason: str,
) -> list[Task]:
    """把这台仪器上还没结束的执行流水在故障时刻收口并暂停任务，返回被暂停的任务。"""
    segments = _open_segments_on_instrument(db, instrument_id)
    for segment in segments:
        _interrupt_task(db, segment, instrument_id, interrupted_at, reason)
    if segments:
        db.flush()
    return [segment.task for segment in segments]


def fault_interrupted_task_ids(
    db,
    instrument_id: int,
    reported_at: datetime,
) -> set[int]:
    """这次故障暂停、现在仍处于暂停的任务。

    维修完成时要把它们的剩余工时按真实的维修完成时间重排，所以得先认出是哪几个。
    任务**保持暂停**，由人回到工位后自己点继续——这与插单暂停一致，系统不替人
    宣布活已经接着干起来了。同一台仪器上因为等样品之类原因人工暂停的任务不在
    这个名单里，维修完成不该顺手动它们。
    """
    return {
        segment.task_id
        for segment in _fault_interrupted_segments(db, instrument_id, reported_at)
        if segment.task.status == "paused"
    }


def resume_fault_interrupted_tasks(
    db,
    task_ids: set[int],
    resumed_at: datetime,
    operator_id: int | None = None,
) -> list[Task]:
    """维修完成后把这次故障暂停的任务重新开起来。

    仪器修好活就该接着干，恢复时刻用真实的维修完成时间。一台仪器同一时刻只可能
    有一个任务在做，所以正常只会恢复一个；万一有第二个，它会被"仪器上还有任务
    没结束"挡住，那时保持暂停并记日志，不能让整个故障关闭失败。
    """
    resumed = []
    for task in _resumable_tasks(db, task_ids):
        slot = _resume_slot(task, resumed_at)
        if slot is None:
            _logger.warning(
                "fault_resume_no_slot task_id=%s resumed_at=%s", task.id, resumed_at,
            )
            continue
        try:
            start_task_execution(
                db,
                slot.id,
                operator_id,
                allow_queue_insert=True,
                advance_schedule=True,
                started_at=resumed_at,
            )
        except TaskExecutionInvalidError:
            _logger.warning(
                "fault_resume_rejected task_id=%s slot_id=%s", task.id, slot.id,
                exc_info=True,
            )
            continue
        resumed.append(task)
    return resumed


def _resumable_tasks(db, task_ids: set[int]) -> list[Task]:
    if not task_ids:
        return []
    return (
        db.query(Task)
        .filter(Task.id.in_(task_ids), Task.status == "paused")
        .order_by(Task.id)
        .all()
    )


def _resume_slot(task: Task, resumed_at: datetime) -> TimeSlot | None:
    """恢复要落在的时间槽：最早那个还没开始、且没结束在恢复时刻之前的。"""
    candidates = sorted(
        (
            slot for slot in task.time_slots
            if slot.lifecycle_status == "active"
            and slot.actual_start is None
            and slot.plan_end >= resumed_at
        ),
        key=lambda slot: (slot.plan_start, slot.id),
    )
    return candidates[0] if candidates else None


def _fault_interrupted_segments(
    db,
    instrument_id: int,
    reported_at: datetime,
) -> list[TaskExecutionSegment]:
    return (
        db.query(TaskExecutionSegment)
        .filter(
            TaskExecutionSegment.instrument_id == instrument_id,
            TaskExecutionSegment.end_reason == INTERRUPT_END_REASON,
            TaskExecutionSegment.ended_at >= reported_at,
        )
        .order_by(TaskExecutionSegment.ended_at, TaskExecutionSegment.id)
        .all()
    )


def _open_segments_on_instrument(db, instrument_id: int) -> list[TaskExecutionSegment]:
    return (
        db.query(TaskExecutionSegment)
        .join(Task, Task.id == TaskExecutionSegment.task_id)
        .filter(
            TaskExecutionSegment.ended_at.is_(None),
            TaskExecutionSegment.instrument_id == instrument_id,
            Task.status == "running",
        )
        .order_by(TaskExecutionSegment.started_at, TaskExecutionSegment.id)
        .all()
    )


def _interrupt_task(
    db,
    segment: TaskExecutionSegment,
    instrument_id: int,
    interrupted_at: datetime,
    reason: str,
) -> None:
    task = segment.task
    _accrue_executed_minutes(db, task, segment, interrupted_at)
    _close_started_slots(db, task, instrument_id, interrupted_at)
    segment.ended_at = interrupted_at
    segment.end_reason = INTERRUPT_END_REASON
    segment.pause_reason = reason
    task.status = "paused"


def _accrue_executed_minutes(
    db,
    task: Task,
    segment: TaskExecutionSegment,
    interrupted_at: datetime,
) -> None:
    """按工作日历累计这一段实际做出来的工时。

    与暂停同一口径：墙钟差值会把隔夜和周末也算成工时，任务看起来已经做完，
    重排时却只排剩下的几十分钟。
    """
    hours = working_hours_between(
        db, segment.started_at, interrupted_at, segment.instrument_id,
    )
    task.executed_minutes = min(
        planned_task_minutes(task),
        int(task.executed_minutes or 0) + max(0, int(hours * 60)),
    )


def _close_started_slots(
    db,
    task: Task,
    instrument_id: int,
    interrupted_at: datetime,
) -> None:
    for slot in _started_slots(task, instrument_id):
        planned_end = slot.plan_end
        boundary = _plan_boundary(slot, interrupted_at)
        slot.actual_end = interrupted_at
        slot.plan_end = boundary
        slot.status = "completed"
        if boundary < planned_end:
            db.add(_remaining_slot(slot, boundary, planned_end))


def _started_slots(task: Task, instrument_id: int) -> list[TimeSlot]:
    return [
        slot for slot in task.time_slots
        if slot.lifecycle_status == "active"
        and slot.instrument_id == instrument_id
        and slot.actual_start is not None
        and slot.actual_end is None
    ]


def _remaining_slot(slot: TimeSlot, boundary: datetime, planned_end: datetime) -> TimeSlot:
    return TimeSlot(
        task_id=slot.task_id,
        schedule_run_id=slot.schedule_run_id,
        instrument_id=slot.instrument_id,
        plan_start=boundary,
        plan_end=planned_end,
        tier=slot.tier,
        status="scheduled",
    )


def _plan_boundary(slot: TimeSlot, interrupted_at: datetime) -> datetime:
    """截断点取排程颗粒度上的计划时刻。

    被切成多段的任务，中间边界一律用计划时间，只有首段的开始和末段的结束写
    真实时间；截断点落在半小时格子上，剩余那段才仍然是整数个时间单元。
    """
    aligned = _ceil_to_time_unit(interrupted_at)
    return max(slot.plan_start, min(slot.plan_end, aligned))


def _ceil_to_time_unit(moment: datetime) -> datetime:
    truncated = moment.replace(second=0, microsecond=0)
    if truncated < moment:
        truncated += timedelta(minutes=1)
    return truncated + timedelta(minutes=(-truncated.minute) % TIME_UNIT_MINUTES)
