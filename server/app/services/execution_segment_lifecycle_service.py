from __future__ import annotations

from datetime import datetime

from app.domain.errors import DomainConflictError
from app.models import Task, TaskExecutionSegment


class ExecutionSegmentStateError(DomainConflictError):
    pass


def ensure_no_open_execution_segment(task: Task) -> None:
    open_segments = _open_segments(task)
    if open_segments:
        raise ExecutionSegmentStateError(
            f"任务【{task.name}】已有未结束的执行记录，不能重复开始"
        )


def close_open_execution_segment(
    task: Task,
    ended_at: datetime,
    end_reason: str,
    pause_reason: str | None = None,
) -> TaskExecutionSegment | None:
    open_segments = _open_segments(task)
    if len(open_segments) > 1:
        raise ExecutionSegmentStateError(
            f"任务【{task.name}】存在多条未结束的执行记录，请先修复执行数据"
        )
    if not open_segments:
        return None
    segment = open_segments[0]
    if ended_at < segment.started_at:
        raise ExecutionSegmentStateError(
            f"任务【{task.name}】的执行结束时间早于开始时间"
        )
    segment.ended_at = ended_at
    segment.end_reason = end_reason
    segment.pause_reason = pause_reason
    return segment


def _open_segments(task: Task) -> list[TaskExecutionSegment]:
    return sorted(
        (segment for segment in task.execution_segments if segment.ended_at is None),
        key=lambda segment: (segment.started_at, segment.id or 0),
    )
