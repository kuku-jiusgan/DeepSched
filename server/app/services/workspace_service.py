from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from app.domain.task_schedule import (
    actual_task_window,
    planned_task_window,
    select_actionable_segment,
)
from app.domain.task_status import resolve_task_execution_status
from app.repositories.workspace_repository import (
    get_active_user,
    list_agenda_slots,
    list_delay_logs,
    list_recent_pause_switch_logs,
    list_tasks_by_ids,
    list_workspace_tasks,
    workspace_segments,
)
from app.schemas.workspace_schemas import (
    TaskWindowOut,
    WorkspaceDelayOut,
    WorkspaceResumePriorityOut,
    WorkspaceSegmentOut,
    WorkspaceTaskOut,
    AgendaAssigneeOut,
    AgendaItemOut,
    AgendaOut,
)
from app.services.task_execution_service import TaskExecutionInvalidError, ensure_paused_state_consistent
from app.services.project_actual_hours_service import task_actual_hours_map
from app.services.task_progress_service import planned_task_hours, planned_task_minutes
from app.services.user_role_service import has_any_role


AGENDA_MANAGER_ROLES = {"系统管理员", "项目管理员", "分析所所长", "技术组长"}
MAX_AGENDA_DAYS = 90


class WorkspaceAgendaInvalidError(Exception):
    pass


class WorkspaceAgendaPermissionError(Exception):
    pass


def get_workspace_tasks(db, user, now: datetime | None = None) -> list[WorkspaceTaskOut]:
    current_time = now or datetime.now()
    tasks = list_workspace_tasks(db, user)
    actual_hours_by_task = task_actual_hours_map(db, [task.id for task in tasks])
    task_segments = {task.id: workspace_segments(task) for task in tasks}
    slot_ids = [slot.id for segments in task_segments.values() for slot in segments]
    delay_by_slot = _delay_details_by_slot(list_delay_logs(db, slot_ids))
    resume_priority_by_task = _resume_priorities(db)

    return [
        _workspace_task_out(
            task,
            task_segments[task.id],
            delay_by_slot,
            resume_priority_by_task,
            current_time,
            actual_hours_by_task.get(task.id),
        )
        for task in tasks
    ]


def _workspace_task_out(
    task, segments, delay_by_slot, resume_priority_by_task, now: datetime, actual_duration_hours: float | None
) -> WorkspaceTaskOut:
    planned_start, planned_end = planned_task_window(segments)
    actual_start, actual_end = actual_task_window(segments)
    if not _has_actual_duration(task, segments):
        actual_duration_hours = None
    actionable = select_actionable_segment(segments, now)
    delay_detail = _task_delay_detail(task, actionable, segments, delay_by_slot)

    execution_status = _workspace_execution_status(task, segments, actionable)

    return WorkspaceTaskOut(
        task_id=task.id,
        task_name=task.name,
        top_level_task_name=_top_level_task_name(task),
        task_type=task.task_type,
        assignee_id=task.assignee_id,
        assignee_name=task.assignee.display_name if task.assignee else None,
        project_id=task.project_id,
        project_name=task.project.name if task.project else None,
        project_code=task.project.code if task.project else None,
        execution_status=execution_status,
        est_duration_hours=planned_task_hours(task),
        completion_ready=int(task.executed_minutes or 0) >= planned_task_minutes(task),
        actual_duration_hours=actual_duration_hours,
        task_window=TaskWindowOut(start=planned_start, end=planned_end),
        actual_window=TaskWindowOut(start=actual_start, end=actual_end),
        actionable_slot=_segment_out(actionable) if actionable else None,
        segments=[_segment_out(segment) for segment in segments],
        delay=WorkspaceDelayOut(status=task.delay_status, **delay_detail),
        resume_priority=resume_priority_by_task.get(task.id),
    )


def _workspace_execution_status(task, segments, actionable) -> str:
    if task.status == "paused" and _is_inconsistent_paused_task(task):
        return "interrupted"
    if actionable and actionable.status in {"paused", "interrupted"}:
        return actionable.status
    return resolve_task_execution_status(task)


def _top_level_task_name(task) -> str | None:
    current = task
    visited: set[int] = set()
    while current.parent is not None and current.id not in visited:
        visited.add(current.id)
        current = current.parent
    return current.name if current is not task else None


def get_workspace_agenda(
    db,
    user,
    start_date: date,
    end_date: date,
    assignee_id: int | None = None,
    today: date | None = None,
) -> AgendaOut:
    _validate_agenda_range(start_date, end_date)
    can_select_assignee = has_any_role(user, AGENDA_MANAGER_ROLES)
    target_id = assignee_id or user.id
    if target_id != user.id and not can_select_assignee:
        raise WorkspaceAgendaPermissionError("无权查看其他人员的安排")
    assignee = get_active_user(db, target_id)
    if assignee is None:
        raise WorkspaceAgendaInvalidError("所选人员不存在或已停用")
    current_date = today or date.today()
    start_at = datetime.combine(start_date, time.min)
    end_at = datetime.combine(end_date + timedelta(days=1), time.min)
    today_start = (
        datetime.combine(current_date, time.min)
        if start_date <= current_date <= end_date
        else None
    )
    today_end = today_start + timedelta(days=1) if today_start is not None else None
    slots = list_agenda_slots(db, target_id, start_at, end_at, today_start, today_end)
    return AgendaOut(
        start_date=start_date,
        end_date=end_date,
        assignee=AgendaAssigneeOut(id=assignee.id, display_name=assignee.display_name),
        can_select_assignee=can_select_assignee,
        items=[item for slot in slots if (item := _agenda_item(slot)) is not None],
    )


def _validate_agenda_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise WorkspaceAgendaInvalidError("开始日期不能晚于结束日期")
    if (end_date - start_date).days + 1 > MAX_AGENDA_DAYS:
        raise WorkspaceAgendaInvalidError(f"查询范围不能超过 {MAX_AGENDA_DAYS} 天")


def _agenda_item(slot) -> AgendaItemOut | None:
    task = slot.task
    project = task.project
    instrument = slot.instrument
    segments = workspace_segments(task)
    actual_start, actual_end = _slot_actual_window(slot)
    task_plan_end = max(
        (
            item.plan_end for item in task.time_slots
            if item.lifecycle_status == "active"
            and item.status in {"scheduled", "running", "paused", "blocked", "interrupted"}
        ),
        default=slot.plan_end,
    )
    if slot.status == "completed" and (actual_start is None or actual_end is None):
        return None
    return AgendaItemOut(
        slot_id=slot.id,
        task_id=task.id,
        task_name=task.name,
        top_level_task_name=_top_level_task_name(task),
        task_status=task.status,
        slot_status=slot.status,
        execution_status=_workspace_execution_status(task, segments, slot),
        project_id=project.id,
        project_code=project.code,
        project_name=project.name,
        instrument_id=instrument.id if instrument else None,
        instrument_code=instrument.code if instrument else None,
        instrument_name=instrument.name if instrument else None,
        plan_start=actual_start if slot.status == "completed" else slot.plan_start,
        plan_end=actual_end if slot.status == "completed" else slot.plan_end,
        task_plan_end=task_plan_end,
        actual_start=actual_start,
        actual_end=actual_end,
    )


def _slot_actual_window(slot):
    if slot.actual_start:
        return slot.actual_start, slot.actual_end
    for segment in slot.task.execution_segments:
        if segment.slot_id == slot.id and segment.started_at and segment.ended_at:
            return segment.started_at, segment.ended_at
    return None, None


def _resume_priorities(db) -> dict[int, WorkspaceResumePriorityOut]:
    source_id_by_target: dict[int, int] = {}
    for log in list_recent_pause_switch_logs(db):
        detail = _audit_detail(log.detail)
        target_id = detail.get("target_task_id")
        source_id = detail.get("source_task_id", log.target_id)
        if isinstance(target_id, int) and isinstance(source_id, int):
            source_id_by_target.setdefault(target_id, source_id)

    source_tasks = {
        task.id: task
        for task in list_tasks_by_ids(db, set(source_id_by_target.values()))
        if task.status == "paused"
    }
    result: dict[int, WorkspaceResumePriorityOut] = {}
    for target_id, source_id in source_id_by_target.items():
        source = source_tasks.get(source_id)
        if not source:
            continue
        result[target_id] = WorkspaceResumePriorityOut(
            task_id=source.id,
            task_name=source.name,
            project_id=source.project_id,
            project_name=source.project.name if source.project else None,
            project_code=source.project.code if source.project else None,
        )
    return result


def _is_inconsistent_paused_task(task) -> bool:
    try:
        ensure_paused_state_consistent(task)
    except TaskExecutionInvalidError:
        return True
    return False


def _has_actual_duration(task, segments) -> bool:
    if any(segment.started_at is not None for segment in task.execution_segments):
        return True
    return any(
        segment.actual_start is not None
        and segment.status in {"completed", "running"}
        for segment in segments
    )


def _segment_out(segment) -> WorkspaceSegmentOut:
    instrument = segment.instrument
    return WorkspaceSegmentOut(
        id=segment.id,
        instrument_id=segment.instrument_id,
        instrument_name=instrument.name if instrument else None,
        instrument_code=instrument.code if instrument else None,
        effective_work_end=instrument.effective_work_end if instrument else None,
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
