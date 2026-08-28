from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.task_slot_transition_worker import advance_running_tasks


class _Query:
    def __init__(self, tasks):
        self.tasks = tasks

    def filter(self, *_args):
        return self

    def all(self):
        return self.tasks


class _Db:
    def __init__(self, tasks):
        self.tasks = tasks
        self.commits = 0

    def query(self, *_args):
        return _Query(self.tasks)

    def commit(self):
        self.commits += 1


def _slot(slot_id, start, end, status="scheduled", actual_start=None):
    return SimpleNamespace(
        id=slot_id,
        plan_start=start,
        plan_end=end,
        status=status,
        lifecycle_status="active",
        actual_start=actual_start,
        actual_end=None,
    )


def _task(slots):
    return SimpleNamespace(status="running", time_slots=slots)


def test_does_not_start_first_slot_without_start_history():
    start = datetime(2026, 8, 27, 8)
    task = _task([_slot(1, start, start + timedelta(hours=2))])
    db = _Db([task])

    assert advance_running_tasks(db, start + timedelta(minutes=30)) == 0
    assert task.time_slots[0].status == "scheduled"


def test_advances_slots_and_keeps_final_running_after_end():
    start = datetime(2026, 8, 27, 8)
    first = _slot(1, start, start + timedelta(hours=2), "running", start)
    final = _slot(2, start + timedelta(days=1), start + timedelta(days=1, hours=2))
    task = _task([first, final])
    db = _Db([task])

    assert advance_running_tasks(db, start + timedelta(days=1, hours=1)) == 2
    assert first.status == "completed"
    assert final.status == "running"

    assert advance_running_tasks(db, start + timedelta(days=1, hours=3)) == 0
    assert final.status == "running"
