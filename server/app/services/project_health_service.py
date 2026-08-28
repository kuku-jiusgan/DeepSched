from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.models import AuditLog, Project, Task, TimeSlot
from app.schemas.project_health_schemas import ProjectHealthOut
from app.services.project_status_service import calculate_project_status
from app.services.project_pending_workload_service import (
    PendingWorkload,
    pending_approval_workload,
)
from app.services.schedule_working_time_service import advance_working_hours
from app.domain.task_status import resolve_task_execution_status


HEALTH_EVENT_ACTIONS = {
    "task_delay_reported": "任务延期",
    "schedule_rescheduled": "排程重新优化",
    "schedule_insert_confirmed": "插单排程确认",
    "schedule_generated": "生成排程",
    "project_plan_drafts_committed": "项目计划保存",
}
OPEN_STATUSES = {"pending", "scheduled", "running", "paused", "blocked", "interrupted"}
COMPLETED_STATUSES = {"done", "completed"}


def get_project_health(
    db,
    project: Project,
    pending_workload: PendingWorkload | None = None,
) -> ProjectHealthOut:
    tasks = _leaf_tasks(project.tasks or [])
    slots = [slot for task in tasks for slot in task.time_slots]
    now = datetime.now()
    delay_details = _delay_details(db, {task.id for task in tasks})
    due_date = project.end_date
    if pending_workload is None:
        pending_workload = pending_approval_workload(db, {project.id}).get(
            project.id, PendingWorkload(),
        )
    predicted_end = _predicted_end(db, tasks, slots, due_date, pending_workload, now)
    counts = _task_counts(tasks)
    schedule_state = _schedule_state(tasks, slots, counts)
    delivery_status, days_delta = _delivery_status(predicted_end, due_date, now)
    delayed = [_task_item(task, slots, now, delay_details) for task in tasks if _delay_days(task, slots, now, delay_details) > 0]
    delayed.sort(key=lambda item: (-item["delay_days"], item["plan_end"] or datetime.max))
    delayed_over_three = [item for item in delayed if item["delay_days"] > 3]
    due_this_week = _due_this_week(tasks, slots, now, delay_details)
    blockers = _blockers(tasks, slots, now, delay_details)
    health_score, health_level = _health_score(delivery_status, counts, delayed_over_three, blockers, schedule_state)
    timeline = _timeline(db, project.id, project, tasks, slots, now)
    return ProjectHealthOut(
        project_id=project.id,
        project_code=project.code,
        project_name=project.name,
        client_name=project.client_name,
        manager_name=project.manager_name,
        start_date=project.start_date,
        end_date=project.end_date,
        summary={
            "project_status": calculate_project_status(project),
            "health_score": health_score,
            "health_level": health_level,
            "delivery_status": delivery_status,
            "due_date": due_date,
            "predicted_end": predicted_end,
            "days_delta": days_delta,
            "schedule_state": schedule_state,
            "metric_mode": timeline["metric_mode"],
            "task_counts": counts,
        },
        due_this_week_open=due_this_week,
        delayed_over_three_days=delayed_over_three,
        blockers=blockers,
        timeline=timeline["timeline"],
        arrangement_items=_arrangement_items(tasks),
    )


def _leaf_tasks(tasks: list[Task]) -> list[Task]:
    parent_ids = {task.parent_id for task in tasks if task.parent_id is not None}
    return [task for task in tasks if task.id not in parent_ids]


def _task_counts(tasks: list[Task]) -> dict[str, int]:
    return {
        "total": len(tasks),
        "completed": sum(task.status in COMPLETED_STATUSES for task in tasks),
        "running": sum(task.status == "running" for task in tasks),
        "pending": sum(task.status in {"pending", "scheduled"} for task in tasks),
        "blocked": sum(task.status in {"blocked", "interrupted", "paused", "waiting_external"} for task in tasks),
        "delayed": sum(task.delay_status == "delayed" for task in tasks),
    }


def _predicted_end(
    db,
    tasks: list[Task],
    slots: list[TimeSlot],
    due_date: datetime | None,
    pending_workload: PendingWorkload,
    now: datetime,
) -> datetime | None:
    # 已排时间槽与未排任务的交期必须取并集。原先的 or 短路意味着：只要项目
    # 有任意一个已排任务，未排任务的交期就被整体丢弃，"还没排进去的工作"
    # 因此完全不体现在交付预测里。
    open_slots = [slot.plan_end for slot in slots if slot.task.status not in COMPLETED_STATUSES]
    unscheduled_due = [task.latest_due for task in tasks if task.status not in COMPLETED_STATUSES and task.latest_due]
    values = [*open_slots, *unscheduled_due] or ([due_date] if due_date else [])
    predicted_end = max(values) if values else None
    return _append_pending_workload(db, predicted_end, pending_workload, now)


def _append_pending_workload(
    db,
    predicted_end: datetime | None,
    pending_workload: PendingWorkload,
    now: datetime,
) -> datetime | None:
    """把"签批通过后才会排程"的工时接在预测完工时间之后。

    这段推演沿工作日历前推，不考虑其它项目对同一台仪器或同一个人的资源竞争，
    因此偏乐观：它能说明"肯定来不及"，不能保证"一定来得及"。
    """
    if pending_workload.hours <= 0:
        return predicted_end
    tail_start = max(
        [value for value in (predicted_end, pending_workload.gate_expected_at, now) if value],
    )
    return advance_working_hours(db, tail_start, pending_workload.hours)


def _schedule_state(tasks: list[Task], slots: list[TimeSlot], counts: dict[str, int]) -> str:
    if counts["completed"] == counts["total"] and tasks:
        return "completed"
    if counts["running"]:
        return "executing"
    if any(task.schedule_dirty for task in tasks):
        return "dirty"
    return "scheduled" if slots else "not_scheduled"


def _delivery_status(predicted_end: datetime | None, due_date: datetime | None, now: datetime) -> tuple[str, int]:
    if not due_date or not predicted_end:
        return "at_risk", 0
    delta = (predicted_end.date() - due_date.date()).days
    if delta > 0:
        return "overdue", delta
    if (due_date.date() - now.date()).days <= 3:
        return "at_risk", delta
    return "on_time", delta


def _delay_days(task: Task, slots: list[TimeSlot], now: datetime, delay_details: dict[int, dict]) -> float:
    task_slots = [slot for slot in slots if slot.task_id == task.id]
    delay_hours = float(delay_details.get(task.id, {}).get("delay_hours") or 0)
    if delay_hours:
        return delay_hours / 24
    if task.delay_status == "delayed" and task_slots:
        latest_end = max(slot.plan_end for slot in task_slots)
        if task.status not in COMPLETED_STATUSES and latest_end < now:
            return max(0, (now - latest_end).total_seconds() / 86400)
    return 0


def _task_item(task: Task, slots: list[TimeSlot], now: datetime, delay_details: dict[int, dict]) -> dict:
    task_slots = [slot for slot in slots if slot.task_id == task.id]
    return {
        "task_id": task.id,
        "task_name": task.name,
        "status": task.status,
        "plan_start": min((slot.plan_start for slot in task_slots), default=None),
        "plan_end": max((slot.plan_end for slot in task_slots), default=task.latest_due),
        "actual_start": min((slot.actual_start for slot in task_slots if slot.actual_start), default=None),
        "actual_end": max((slot.actual_end for slot in task_slots if slot.actual_end), default=None),
        "delay_days": round(_delay_days(task, slots, now, delay_details), 1),
        "delay_reason": delay_details.get(task.id, {}).get("reason"),
        "assignee_name": task.assignee_name,
    }


def _due_this_week(tasks: list[Task], slots: list[TimeSlot], now: datetime, delay_details: dict[int, dict]) -> list[dict]:
    week_end = now + timedelta(days=7 - now.weekday())
    items = [
        _task_item(task, slots, now, delay_details)
        for task in tasks
        if task.status not in COMPLETED_STATUSES
        and (_task_item(task, slots, now, delay_details)["plan_end"] or datetime.max) <= week_end
    ]
    return sorted(items, key=lambda item: item["plan_end"] or datetime.max)


def _blockers(tasks: list[Task], slots: list[TimeSlot], now: datetime, delay_details: dict[int, dict]) -> list[dict]:
    result = []
    for task in tasks:
        item = _task_item(task, slots, now, delay_details)
        if item["delay_days"] > 0:
            item["blocker_type"] = "delayed"
        elif task.status == "waiting_external":
            item["blocker_type"] = "waiting_external"
        elif task.status not in COMPLETED_STATUSES and not any(slot.task_id == task.id for slot in slots):
            item["blocker_type"] = "unscheduled"
        else:
            continue
        result.append(item)
    return sorted(result, key=lambda item: (-item["delay_days"], item["plan_end"] or datetime.max))


def _health_score(delivery_status, counts, delayed, blockers, schedule_state):
    if counts["total"] == 0:
        return 40, "red"
    score = 100
    if delivery_status == "overdue": score -= 50
    score -= min(30, len(delayed) * 10)
    score -= 15 if any(item["blocker_type"] in {"delayed", "waiting_external"} for item in blockers) else 0
    score -= min(20, sum(item["blocker_type"] == "unscheduled" for item in blockers) * 10)
    score -= 10 if schedule_state == "not_scheduled" and counts["pending"] else 0
    score = max(0, min(100, score))
    return score, "green" if score >= 80 else "yellow" if score >= 50 else "red"


def _timeline(db, project_id, project, tasks, slots, now):
    values = [float(task.est_duration_hours or 0) for task in tasks]
    metric_mode = "estimated_hours" if sum(values) > 0 else "task_count"
    weights = values if metric_mode == "estimated_hours" else [1.0] * len(tasks)
    total = sum(weights)
    start = project.start_date or min((slot.plan_start for slot in slots), default=now)
    end = project.end_date or max((slot.plan_end for slot in slots), default=now)
    if end < start: end = start
    points = []
    cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cursor.date() <= end.date():
        actual = sum(weight for task, weight in zip(tasks, weights) if task.status in COMPLETED_STATUSES and _completion_date(task, slots) and _completion_date(task, slots) <= cursor)
        forecast = sum(weight for task, weight in zip(tasks, weights) if _task_forecast_date(task, slots) and _task_forecast_date(task, slots) <= cursor)
        ratio = 1 if end == start else min(1, max(0, (cursor - start).total_seconds() / (end - start).total_seconds()))
        points.append({"date": cursor, "ideal": round(total * ratio, 2), "actual": round(actual, 2), "forecast": round(forecast, 2)})
        cursor += timedelta(days=1)
    task_ids = {task.id for task in tasks}
    annotations = []
    for log in db_query_audit(db, task_ids, project_id):
        detail = log.detail if isinstance(log.detail, dict) else {}
        annotations.append({"date": log.created_at, "title": HEALTH_EVENT_ACTIONS.get(log.action, log.action), "detail": _event_detail(detail), "task_id": log.target_id if log.target_id in task_ids else None})
    timeline_tasks = [_timeline_task(task, slots) for task in tasks]
    timeline_tasks.sort(key=lambda item: item["plan_start"] or item["actual_start"] or datetime.max)
    return {"metric_mode": metric_mode, "timeline": {"total_value": total, "points": points, "annotations": annotations, "tasks": timeline_tasks}}


def _timeline_task(task, slots):
    task_slots = [slot for slot in slots if slot.task_id == task.id]
    return {
        "task_id": task.id,
        "task_name": task.name,
        "status": task.status,
        "plan_start": min((slot.plan_start for slot in task_slots), default=None),
        "plan_end": max((slot.plan_end for slot in task_slots), default=task.latest_due),
        "actual_start": min((slot.actual_start for slot in task_slots if slot.actual_start), default=None),
        "actual_end": max((slot.actual_end for slot in task_slots if slot.actual_end), default=None),
        "assignee_name": task.assignee_name,
        "is_external_gate": bool(task.is_external_gate),
        "expected_approval_at": task.expected_approval_at,
    }


def _arrangement_items(tasks: list[Task]) -> list[dict]:
    items = []
    for task in tasks:
        task_slots = sorted(task.time_slots, key=lambda slot: (slot.plan_start, slot.id))
        if task_slots:
            items.extend(_arrangement_slot_item(task, slot) for slot in task_slots)
        else:
            items.append(_arrangement_slot_item(task, None))
    return sorted(items, key=_arrangement_sort_key)


def _arrangement_slot_item(task: Task, slot: TimeSlot | None) -> dict:
    instrument = slot.instrument if slot else None
    actual_start, actual_end = _slot_actual_window(task, slot)
    return {
        "slot_id": slot.id if slot else None,
        "task_id": task.id,
        "task_name": task.name,
        "top_level_task_name": _top_level_task_name(task),
        "plan_order": task.plan_order,
        "task_status": resolve_task_execution_status(task),
        "slot_status": slot.status if slot else None,
        "delay_status": task.delay_status or "not_delayed",
        "assignee_id": task.assignee_id,
        "assignee_name": task.assignee_name,
        "instrument_id": instrument.id if instrument else None,
        "instrument_code": instrument.code if instrument else None,
        "instrument_name": instrument.name if instrument else None,
        "plan_start": slot.plan_start if slot else None,
        "plan_end": slot.plan_end if slot else None,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "is_external_gate": bool(task.is_external_gate),
        "expected_approval_at": task.expected_approval_at,
    }


def _slot_actual_window(task: Task, slot: TimeSlot | None):
    if slot and slot.actual_start:
        return slot.actual_start, slot.actual_end
    if slot:
        segments = [segment for segment in task.execution_segments if segment.slot_id == slot.id]
        if segments:
            return (
                min(segment.started_at for segment in segments),
                max((segment.ended_at for segment in segments if segment.ended_at), default=None),
            )
    return None, None


def _top_level_task_name(task: Task) -> str | None:
    current = task
    visited = set()
    while current.parent is not None and current.id not in visited:
        visited.add(current.id)
        current = current.parent
    return current.name if current is not task else None


def _arrangement_sort_key(item: dict):
    anchor = item["plan_start"] or item["expected_approval_at"] or datetime.max
    return anchor, item["plan_order"], item["task_id"], item["slot_id"] or 0


def _completion_date(task, slots):
    return max((slot.actual_end for slot in slots if slot.task_id == task.id and slot.actual_end), default=None)


def _task_forecast_date(task, slots):
    return max((slot.plan_end for slot in slots if slot.task_id == task.id), default=task.latest_due)


def _delay_details(db, task_ids: set[int]) -> dict[int, dict]:
    if not task_ids:
        return {}
    logs = db.query(AuditLog).filter(AuditLog.action == "task_delay_reported").order_by(AuditLog.created_at.asc()).all()
    details: dict[int, dict] = {}
    for log in logs:
        raw = log.detail if isinstance(log.detail, dict) else {}
        task_id = raw.get("task_id")
        if task_id not in task_ids:
            continue
        item = details.setdefault(task_id, {"delay_hours": 0.0, "reason": None})
        item["delay_hours"] += float(raw.get("delay_hours") or 0)
        if raw.get("reason"):
            item["reason"] = str(raw["reason"])
    return details


def db_query_audit(db, task_ids, project_id):
    if not task_ids:
        return []
    logs = db.query(AuditLog).filter(AuditLog.action.in_(HEALTH_EVENT_ACTIONS)).order_by(AuditLog.created_at.asc()).all()
    result = []
    for log in logs:
        detail = log.detail if isinstance(log.detail, dict) else {}
        if log.target_type == "task" and log.target_id in task_ids:
            result.append(log)
        elif detail.get("task_id") in task_ids:
            result.append(log)
        elif log.action in {"schedule_generated", "schedule_rescheduled"} and (
            not detail.get("project_ids") or project_id in detail.get("project_ids", [])
        ):
            result.append(log)
    return result


def _event_detail(detail):
    return "；".join(f"{key}: {value}" for key, value in detail.items() if value) or "已记录项目进展"
