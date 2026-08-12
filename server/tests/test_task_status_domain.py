from dataclasses import dataclass, field

from app.domain.task_status import resolve_task_execution_status


@dataclass
class Segment:
    ended_at: object | None = None


@dataclass
class Task:
    status: str
    execution_segments: list[Segment] = field(default_factory=list)


def test_open_execution_segment_makes_task_running():
    task = Task(status="scheduled", execution_segments=[Segment(ended_at=None)])

    assert resolve_task_execution_status(task) == "running"


def test_completed_task_status_wins_over_open_segments():
    task = Task(status="done", execution_segments=[Segment(ended_at=None)])

    assert resolve_task_execution_status(task) == "completed"


def test_paused_task_without_open_segment_stays_paused():
    task = Task(status="paused", execution_segments=[Segment(ended_at=object())])

    assert resolve_task_execution_status(task) == "paused"
