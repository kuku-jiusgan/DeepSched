from __future__ import annotations

from app.models import ScheduleCalendarSnapshot


def get_schedule_diagnostic(db, schedule_run_id: str) -> dict | None:
    snapshot = db.query(ScheduleCalendarSnapshot).filter(
        ScheduleCalendarSnapshot.schedule_run_id == schedule_run_id,
    ).first()
    if snapshot is None:
        return None
    return {
        "schedule_run_id": snapshot.schedule_run_id,
        "horizon_start": snapshot.horizon_start,
        "horizon_end": snapshot.horizon_end,
        "generated_at": snapshot.created_at,
        "diagnostic": snapshot.replan_diagnostic,
    }
