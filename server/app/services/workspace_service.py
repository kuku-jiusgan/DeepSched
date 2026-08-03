from __future__ import annotations

import json
from datetime import datetime

from app.domain.task_schedule import (
    actual_task_window,
    planned_task_window,
    select_actionable_segment,
)
from app.repositories.workspace_repository import (
    list_delay_logs,
    list_workspace_tasks,
    workspace_segments,
)
from app.schemas.workspace_schemas import (
    TaskWindowOut,
    WorkspaceDelayOut,
    WorkspaceSegmentOut,
    WorkspaceTaskOut,
)


def get_workspace_tasks(db, user, now: datetime | None = None) -> list[WorkspaceTaskOut]:
    current_time = now or datetime.now()
    tasks = list_workspace_tasks(db, user)
    task_segments = {task.id: workspace_segments(task) for task in tasks}
    slot_ids = [slot.id for segments in task_segments.values() for slot in segments]
    delay_by_slot = _delay_details_by_slot(list_delay_logs(db, slot_ids))

    return [
        _workspace_task_out(task, task_segments[task.id], delay_by_slot, current_time)
        for task in tasks
    ]


def _workspace_task_out(task, segments, delay_by_slot, now: datetime) -> WorkspaceTaskOut:
    planned_start, planned_end = planned_task_window(segments)
    actual_start, actual_end = actual_task_window(segments)
    actual_duration_hours = _actual_duration_hours(task, segments)
    actionable = select_actionable_segment(segments, now)
    delay_detail = _task_delay_detail(task, actionable, segments, delay_by_slot)

    return WorkspaceTaskOut(
        task_id=task.id,
        task_name=task.name,
        task_type=task.task_type,
        assignee_id=task.assignee_id,
        assignee_name=task.assignee.display_name if task.assignee else None,
        project_id=task.project_id,
        project_name=task.project.name if task.project else None,
        project_code=task.project.code if task.project else None,
        execution_status=task.status,
        est_duration_hours=task.est_duration_hours,
        actual_duration_hours=actual_duration_hours,
        task_window=TaskWindowOut(start=planned_start, end=planned_end),
        actual_window=TaskWindowOut(start=actual_start, end=actual_end),
        actionable_slot=_segment_out(actionable) if actionable else None,
        segments=[_segment_out(segment) for segment in segments],
        delay=WorkspaceDelayOut(status=task.delay_status, **delay_detail),
    )


def _actual_duration_hours(task, segments) -> float | None:
    execution_segments = [
        segment for segment in task.execution_segments
        if segment.ended_at is not None
    ]
    if execution_segments:
        total_seconds = sum(
            (segment.ended_at - segment.started_at).total_seconds()
            for segment in execution_segments
        )
        return round(total_seconds / 3600, 2)
    completed_segments = [
        segment for segment in segments
        if segment.actual_start is not None and segment.actual_end is not None
    ]
    if not completed_segments:
        return None
    total_seconds = sum(
        (segment.actual_end - segment.actual_start).total_seconds()
        for segment in completed_segments
    )
    return round(total_seconds / 3600, 2)


def _segment_out(segment) -> WorkspaceSegmentOut:
    instrument = segment.instrument
    return WorkspaceSegmentOut(
        id=segment.id,
        instrument_id=segment.instrument_id,
        instrument_name=instrument.name if instrument else None,
        instrument_code=instrument.code if instrument else None,
        plan_start=segment.plan_start,
        plan_end=segment.plan_end,
        actual_start=segment.actual_start,
        actual_end=segment.actual_end,
        tier=segment.tier,
        status=segment.status,
    )


def _task_delay_detail(task, actionable, segments, delay_by_slot) -> dict:
    if task.delay_status != "delayed":
        return {"hours": None, "reason": None, "reported_at": None}
    preferred_slots = ([actionable] if actionable else []) + list(reversed(segments))
    seen: set[int] = set()
    for slot in preferred_slots:
        if slot.id in seen:
            continue
        seen.add(slot.id)
        detail = delay_by_slot.get(slot.id)
        if detail and detail.get("task_id") == task.id:
            return {
                "hours": detail.get("delay_hours"),
                "reason": detail.get("reason"),
                "reported_at": detail.get("reported_at"),
            }
    return {"hours": None, "reason": None, "reported_at": None}


def _delay_details_by_slot(logs) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for log in logs:
        if log.target_id in result:
            continue
        detail = _audit_detail(log.detail)
        detail["reported_at"] = log.created_at
        result[log.target_id] = detail
    return result


def _audit_detail(detail) -> dict:
    if isinstance(detail, dict):
        return dict(detail)
    if isinstance(detail, str):
        try:
            parsed = json.loads(detail)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
