from __future__ import annotations

from app.services.instrument_bridge_sync_service import stale_bridge_reservation_ids
from app.services.schedule_conflict_service import (
    ScheduleConflictError,
    ensure_no_dependency_conflicts,
    ensure_no_human_conflicts,
    ensure_no_instrument_conflicts,
)


def ensure_replan_consistent(
    db,
    schedule_run_id: str,
    business_dependencies: list[tuple[int, int]],
    queue_dependencies: list[tuple[int, int]],
) -> None:
    """Validate every derived constraint after one solver schedule run is persisted."""
    ensure_no_instrument_conflicts(db, schedule_run_id)
    ensure_no_human_conflicts(db, schedule_run_id)
    ensure_no_dependency_conflicts(db, business_dependencies, schedule_run_id)
    ensure_no_dependency_conflicts(
        db,
        queue_dependencies,
        schedule_run_id,
        task_slots_from_run_only=True,
    )
    _ensure_current_bridge_reservations(db, schedule_run_id)


def _ensure_current_bridge_reservations(db, schedule_run_id: str) -> None:
    stale_reservation_ids = stale_bridge_reservation_ids(db, schedule_run_id)
    if stale_reservation_ids:
        raise ScheduleConflictError(
            "仪器桥接占用记录与当前排程不一致，请重新排程"
        )
