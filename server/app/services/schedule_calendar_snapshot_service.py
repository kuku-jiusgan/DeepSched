from __future__ import annotations

from app.models import ScheduleCalendarSnapshot, ScheduleRule


def save_schedule_calendar_snapshot(
    db,
    schedule_run_id: str,
    horizon_start,
    horizon_end,
    working_params: dict,
    calendar_days: dict,
    maintenance_windows: list,
) -> None:
    rules = db.query(ScheduleRule).order_by(ScheduleRule.code).all()
    db.add(ScheduleCalendarSnapshot(
        schedule_run_id=schedule_run_id,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        working_hours=working_params,
        calendar_days={key.isoformat(): value for key, value in calendar_days.items()},
        maintenance_windows=[
            {"instrument_id": instrument_id, "start_unit": start, "end_unit": end}
            for instrument_id, (start, end) in maintenance_windows
        ],
        rule_versions={
            rule.code: {
                "enabled": bool(rule.is_enabled),
                "params": rule.params or {},
                "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
            }
            for rule in rules
        },
    ))
