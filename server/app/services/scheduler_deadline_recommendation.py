from __future__ import annotations

from datetime import datetime, time, timedelta
from heapq import heappop, heappush
from itertools import combinations
from time import monotonic

from app.models import Project
from app.services.scheduler_helpers import TIME_UNIT_MINUTES, datetime_to_units, units_to_datetime

MAX_RECOMMENDATIONS = 20
SEARCH_TIME_LIMIT_SECONDS = 120


def enumerate_verified_date_adjustments(
    db, scheduler, project_ids: list[int], original_deadlines: dict[int, datetime],
    horizon_end: datetime, generate_kwargs: dict, project_labels: dict[int, str] | None = None,
) -> list[dict]:
    """Enumerate minimal project-deadline adjustments verified by the solver."""
    project_ids = [project_id for project_id in project_ids if project_id in original_deadlines]
    priorities = _load_project_priorities(db, project_ids)
    candidates = {
        project_id: [
            (original_deadlines[project_id] + timedelta(days=offset)).replace(hour=23, minute=59, second=0, microsecond=0)
            for offset in range(1, (horizon_end.date() - original_deadlines[project_id].date()).days + 1)
            if (original_deadlines[project_id] + timedelta(days=offset)).date() <= horizon_end.date()
        ]
        for project_id in project_ids
    }
    results: list[dict] = []
    search_deadline = monotonic() + SEARCH_TIME_LIMIT_SECONDS
    for size in range(1, len(project_ids) + 1):
        for selected in combinations(project_ids, size):
            if monotonic() >= search_deadline:
                return _sort_results(results)
            if any(set(item["projects"]) < set(selected) for item in results):
                continue
            adjustment = _first_verified_adjustment(
                db, scheduler, selected, candidates, generate_kwargs, search_deadline,
            )
            if adjustment:
                results.append(_format_adjustment(
                    adjustment, original_deadlines, project_labels or {}, priorities,
                ))
                if len(results) >= MAX_RECOMMENDATIONS:
                    return _sort_results(results)
    return _sort_results(results)


def _first_verified_adjustment(
    db, scheduler, selected, candidates, generate_kwargs, search_deadline,
):
    if any(not candidates[project_id] for project_id in selected):
        return None
    initial = tuple(0 for _ in selected)
    queue = [(len(selected), initial)]
    visited = {initial}
    while queue:
        if monotonic() >= search_deadline:
            return None
        _cost, indexes = heappop(queue)
        adjustment = {
            project_id: candidates[project_id][indexes[index]]
            for index, project_id in enumerate(selected)
        }
        if _validate_deadlines(db, scheduler, adjustment, generate_kwargs):
            return adjustment
        for index, project_id in enumerate(selected):
            next_indexes = list(indexes)
            next_indexes[index] += 1
            candidate_indexes = tuple(next_indexes)
            if (
                candidate_indexes not in visited
                and next_indexes[index] < len(candidates[project_id])
            ):
                visited.add(candidate_indexes)
                heappush(queue, (sum(item + 1 for item in candidate_indexes), candidate_indexes))
    return None


def _load_project_priorities(db, project_ids: list[int]) -> dict[int, int]:
    query = getattr(db, "query", None)
    if query is None:
        return {}
    return {
        project.id: int(project.priority or 999)
        for project in query(Project).filter(Project.id.in_(project_ids)).all()
    }


def _validate_deadlines(db, scheduler, deadlines: dict[int, datetime], generate_kwargs: dict) -> bool:
    savepoint = db.begin_nested()
    try:
        for project_id, deadline in deadlines.items():
            project = db.query(Project).filter(Project.id == project_id).one()
            project.end_date = deadline
        db.flush()
        return scheduler.generate(
            **generate_kwargs, commit=False, emit_advance_notifications=False,
            include_failure_diagnostics=False, solver_time_limit=5.0,
        ).get("status") == "ok"
    finally:
        savepoint.rollback()


def _format_adjustment(
    adjustment: dict[int, datetime], originals: dict[int, datetime], labels: dict[int, str],
    priorities: dict[int, int] | None = None,
) -> dict:
    changes = [
        {
            "project_id": project_id,
            "project_label": labels.get(project_id, f"项目 {project_id}"),
            "original_deadline": originals[project_id].strftime("%Y-%m-%d"),
            "suggested_deadline": deadline.strftime("%Y-%m-%d"),
            "delay_days": (deadline.date() - originals[project_id].date()).days,
            "project_priority": (priorities or {}).get(project_id, 999),
        }
        for project_id, deadline in sorted(adjustment.items())
    ]
    return {
        "code": "",
        "kind": "verified_date_adjustment",
        "title": "、".join(f"{item['project_label']} 延期 {item['delay_days']} 天" for item in changes),
        "description": "；".join(
            f"{item['project_label']}结题日 {item['original_deadline']} → {item['suggested_deadline']}"
            for item in changes
        ),
        "projects": [item["project_id"] for item in changes],
        "changes": changes,
        "verified": True,
        "verification": "solver",
    }


def _sort_results(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda item: (
        tuple(sorted(
            (-next(change["project_priority"] for change in item["changes"] if change["project_id"] == project_id), project_id)
            for project_id in item["projects"]
        )),
        len(item["projects"]), sum(change["delay_days"] for change in item["changes"]),
        tuple(change["suggested_deadline"] for change in item["changes"]),
    ))[:MAX_RECOMMENDATIONS]


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
                "original_deadline": original_deadline.strftime("%Y-%m-%d"),
                "suggested_deadline": candidate.strftime("%Y-%m-%d"),
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
