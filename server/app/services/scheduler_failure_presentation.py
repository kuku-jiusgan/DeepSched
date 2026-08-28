from __future__ import annotations

from collections import defaultdict
from datetime import datetime


def build_failure_presentation(current_project, groups: list[dict]) -> dict:
    instruments = _instrument_rows(groups)
    occupancy = _occupancy_rows(groups)
    return {
        "project_id": current_project.id,
        "project_label": _project_label(current_project),
        "deadline": _format_datetime(current_project.end_date),
        "days_remaining": max(0, (current_project.end_date.date() - datetime.now().date()).days),
        "instruments": instruments,
        "occupancy": occupancy,
        "recommendations": _release_recommendations(current_project, instruments, occupancy),
    }


def _instrument_rows(groups: list[dict]) -> list[dict]:
    rows = {}
    for group in groups:
        instrument_id = group["instrument_id"]
        row = rows.setdefault(instrument_id, {
            "instrument_id": instrument_id,
            "instrument_label": group["instrument_label"],
            "available_hours": group["available_hours"],
            "occupied_hours": group["occupied_hours"],
            "remaining_hours": group["remaining_hours"],
            "required_hours": 0.0,
            "deficit_hours": 0.0,
        })
        row["available_hours"] = max(row["available_hours"], group["available_hours"])
        row["occupied_hours"] = max(row["occupied_hours"], group["occupied_hours"])
        row["remaining_hours"] = max(0, row["available_hours"] - row["occupied_hours"])
        row["required_hours"] += group["required_hours"]
        row["deficit_hours"] = max(0, row["required_hours"] - row["remaining_hours"])
    return list(rows.values())


def _occupancy_rows(groups: list[dict]) -> list[dict]:
    rows = {}
    for group in groups:
        for detail in group["details"]:
            key = (group["instrument_id"], detail["project_id"])
            current = rows.get(key)
            candidate = {
                "instrument_id": group["instrument_id"],
                "instrument_label": group["instrument_label"],
                "project_id": detail["project_id"],
                "project_label": detail["project_label"],
                "scheduled_hours": detail["scheduled_hours"],
                "bridged_hours": detail["bridged_hours"],
                "forecast_hours": detail["forecast_hours"],
                "total_hours": detail["total_hours"],
            }
            if current is None or candidate["total_hours"] > current["total_hours"]:
                rows[key] = candidate
    return sorted(rows.values(), key=lambda row: (-row["total_hours"], row["project_id"]))


def _release_recommendations(current_project, instruments, occupancy) -> list[dict]:
    occupancy_by_instrument = defaultdict(list)
    for row in occupancy:
        occupancy_by_instrument[row["instrument_id"]].append(row)
    recommendations = []
    for instrument in instruments:
        deficit = instrument["deficit_hours"]
        if deficit <= 0:
            continue
        rows = occupancy_by_instrument[instrument["instrument_id"]]
        for row in rows:
            if row["total_hours"] < deficit:
                continue
            recommendations.append({
                "code": "B",
                "kind": "delay_occupied_project",
                "title": f"延期项目【{row['project_label']}】",
                "description": (
                    f"将项目【{row['project_label']}】整体后移，预计可释放至少 {deficit:g}h，"
                    f"能够覆盖当前仪器缺口。"
                ),
                "instrument_id": instrument["instrument_id"],
                "project_id": row["project_id"],
                "hours": deficit,
                "verified": False,
                "verification": "capacity_lower_bound",
            })
    return recommendations


def _project_label(project) -> str:
    code = getattr(project, "code", None)
    return f"{code} · {project.name}" if code and code != project.name else project.name


def _format_datetime(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "未设置"
