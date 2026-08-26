from __future__ import annotations

from app.services.schedule_conflict_service import (
    find_human_conflicts,
    find_instrument_conflicts,
)


def external_conflict_task_ids(db, schedule_run_id: str, task_ids: set[int]) -> set[int]:
    """Return conflict tasks outside the current replan closure."""
    conflicts = [
        *find_instrument_conflicts(db, schedule_run_id),
        *find_human_conflicts(db, schedule_run_id),
    ]
    conflicted = set()
    for conflict in conflicts:
        conflicted.update((conflict["first_task_id"], conflict["second_task_id"]))
    return conflicted - task_ids
