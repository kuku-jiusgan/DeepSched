from __future__ import annotations

from datetime import datetime, time, timedelta

from app.models import Project
from app.services.scheduler_helpers import TIME_UNIT_MINUTES, datetime_to_units, units_to_datetime


def verified_current_project_deadline(
    db,
    scheduler,
    project_id: int,
    original_deadline: datetime,
    horizon_start: datetime,
    horizon_end: datetime,
    instrument_prefix_sums,
    failure: dict,
    generate_kwargs: dict,
) -> dict | None:
    lower_date = _capacity_lower_date(
        original_deadline, horizon_start, horizon_end,
        instrument_prefix_sums, failure.get("instruments", []),
    )
    if lower_date is None:
        return None
    return verified_current_project_deadline_from_lower_date(
        db, scheduler, project_id, original_deadline, lower_date, horizon_end, generate_kwargs,
    )


def verified_current_project_deadline_from_lower_date(
    db,
    scheduler,
    project_id: int,
    original_deadline: datetime,
    lower_date,
    horizon_end: datetime,
    generate_kwargs: dict,
) -> dict | None:
    candidate_date = lower_date
    while candidate_date <= horizon_end.date():
        candidate = datetime.combine(candidate_date, time(23, 59))
        result = _validate_deadline(
            db, scheduler, project_id, candidate, generate_kwargs,
        )
        if result.get("status") == "ok":
            return {
                "code": "C",
                "kind": "extend_current_project",
                "title": "延长本次项目结题日",
                "description": (
                    f"将本次项目结题日最少延长至 {candidate:%Y-%m-%d}，"
                    "该日期已通过完整排程约束验证。"
                ),
                "project_id": project_id,
                "original_deadline": original_deadline.strftime("%Y-%m-%d %H:%M"),
                "suggested_deadline": candidate.strftime("%Y-%m-%d %H:%M"),
                "verified": True,
                "verification": "solver",
            }
        if result.get("solver_status") == "UNKNOWN":
            return None
        candidate_date += timedelta(days=1)
    return None


def _validate_deadline(db, scheduler, project_id, deadline, generate_kwargs):
    savepoint = db.begin_nested()
    try:
        project = db.query(Project).filter(Project.id == project_id).one()
        project.end_date = deadline
        db.flush()
        return scheduler.generate(
            **generate_kwargs,
            current_project_id=project_id,
            commit=False,
            emit_advance_notifications=False,
            include_failure_diagnostics=False,
            solver_time_limit=5.0,
        )
    finally:
        savepoint.rollback()


def _capacity_lower_date(
    original_deadline, horizon_start, horizon_end,
    instrument_prefix_sums, instruments,
):
    start_unit = max(0, datetime_to_units(original_deadline, horizon_start))
    lower_unit = start_unit
    for row in instruments:
        deficit_units = round(row["deficit_hours"] * 60 / TIME_UNIT_MINUTES)
        if deficit_units <= 0:
            continue
        prefix = instrument_prefix_sums.get(row["instrument_id"])
        if not prefix:
            return None
        target = prefix[min(start_unit, len(prefix) - 1)] + deficit_units
        instrument_unit = next(
            (unit for unit in range(start_unit, len(prefix)) if prefix[unit] >= target),
            None,
        )
        if instrument_unit is None:
            return None
        lower_unit = max(lower_unit, instrument_unit)
    lower_date = units_to_datetime(lower_unit, horizon_start).date()
    return max(original_deadline.date() + timedelta(days=1), lower_date)
