from __future__ import annotations

from typing import Iterable, Protocol


COMPLETED_TASK_STATUSES = {"done", "completed"}


class ExecutionSegmentLike(Protocol):
    ended_at: object | None


class TaskLike(Protocol):
    status: str
    execution_segments: Iterable[ExecutionSegmentLike]


def resolve_task_execution_status(task: TaskLike | None) -> str:
    if task is None:
        return "scheduled"
    if task.status in COMPLETED_TASK_STATUSES:
        return "completed"
    if any(segment.ended_at is None for segment in task.execution_segments):
        return "running"
    return task.status or "scheduled"
