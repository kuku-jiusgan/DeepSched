from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import json
from datetime import date, datetime
from app.core.database import get_db
from app.models import TimeSlot, Task, Instrument, Project, AuditLog, User
from app.schemas.schemas import (
    TimeSlotOut, TimeSlotUpdate, TaskStatusUpdate,
    ScheduleGenerateRequest, InsertOrderRequest, InsertOrderPreview, InsertOrderResult,
    RescheduleRequest, TaskDelayRequest, TaskDelayResponse,
    NightRunRequest, TaskActionResponse, TaskCompleteRequest, TaskCompleteResponse,
    TaskPauseRequest, TaskSwitchCandidateOut,
)
from app.services.scheduler import SchedulerService
from app.services.schedule_delay_service import (
    report_task_delay,
)
from app.services.schedule_manual_update_service import (
    ScheduleSlotInvalidError,
    ScheduleSlotNotFoundError,
    update_time_slot,
)
from app.services.schedule_insert_service import (
    ScheduleInsertInvalidError,
    ScheduleInsertNotFoundError,
    confirm_insert as confirm_insert_service,
    preview_insert,
)
from app.services.schedule_night_run_service import (
    record_night_run,
)
from app.services.schedule_reschedule_service import reschedule as reschedule_service
from app.services.schedule_tier_service import roll_schedule_tiers
from app.services.approval_gate_service import scan_approval_deadlines
from app.services.task_execution_service import (
    start_task_execution,
)
from app.services.workspace_service import (
    WorkspaceAgendaInvalidError,
    WorkspaceAgendaPermissionError,
    get_workspace_agenda,
    get_workspace_tasks,
)
from app.services.audit_log_service import record_audit_log
from app.services.workspace_command_service import complete_workspace_task, interrupt_workspace_task
from app.services.task_pause_service import list_switch_candidates, pause_and_switch_task
from app.api.transactions import execute_transaction
from app.schemas.workspace_schemas import AgendaOut, WorkspaceTaskOut
from app.domain.task_schedule import (
    actual_task_window as _task_actual_window,
    select_actionable_segment as _select_workspace_slot,
)
from app.domain.task_status import resolve_task_execution_status
from app.repositories.workspace_repository import (
    filter_workspace_tasks_by_user as _filter_workspace_tasks_by_user,
    latest_open_task_slot as _latest_open_task_slot,
)
from app.api.users import require_authenticated_user
from app.api.access import require_management_user, require_slot_operator

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

@router.get("/timeslots", response_model=List[TimeSlotOut])
def list_timeslots(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    instrument_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    tier: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(TimeSlot)
    if start_date:
        q = q.filter(TimeSlot.plan_end > start_date)
    if end_date:
        q = q.filter(TimeSlot.plan_start < end_date)
    if instrument_id:
        q = q.filter(TimeSlot.instrument_id == instrument_id)
    if project_id:
        q = q.join(Task).filter(Task.project_id == project_id)
    if tier:
        q = q.filter(TimeSlot.tier == tier)
    slots = q.order_by(TimeSlot.plan_start).all()
    return [_enrich_slot(s, db) for s in slots]

@router.put("/timeslots/{slot_id}", response_model=TimeSlotOut)
def update_timeslot(
    slot_id: int,
    data: TimeSlotUpdate,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    try:
        return _enrich_slot(update_time_slot(db, slot_id, data), db)
    except ScheduleSlotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScheduleSlotInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/timeslots/{slot_id}/start", response_model=TaskActionResponse)
def start_task(
    slot_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_slot_operator),
):
    return execute_transaction(db, lambda: start_task_execution(db, slot_id, user.id))


@router.get(
    "/timeslots/{slot_id}/switch-candidates",
    response_model=List[TaskSwitchCandidateOut],
)
def switch_candidates(
    slot_id: int,
    db: Session = Depends(get_db),
    _user=Depends(require_slot_operator),
):
    return list_switch_candidates(db, slot_id)


@router.post("/timeslots/{slot_id}/pause", response_model=TaskActionResponse)
def pause_task(
    slot_id: int,
    data: TaskPauseRequest,
    db: Session = Depends(get_db),
    user=Depends(require_slot_operator),
):
    return execute_transaction(
        db,
        lambda: pause_and_switch_task(db, slot_id, data.reason, user, data.target_slot_id),
    )

@router.post("/timeslots/{slot_id}/complete", response_model=TaskCompleteResponse)
def complete_task(
    slot_id: int,
    data: TaskCompleteRequest = TaskCompleteRequest(),
    db: Session = Depends(get_db),
    _user=Depends(require_slot_operator),
):
    return execute_transaction(
        db,
        lambda: complete_workspace_task(db, slot_id, data.release_instrument),
    )

@router.post("/timeslots/{slot_id}/interrupt", response_model=TaskActionResponse)
def interrupt_task(
    slot_id: int,
    db: Session = Depends(get_db),
    _user=Depends(require_slot_operator),
):
    return execute_transaction(db, lambda: interrupt_workspace_task(db, slot_id))

@router.post("/timeslots/{slot_id}/delay", response_model=TaskDelayResponse)
def delay_task(
    slot_id: int,
    data: TaskDelayRequest,
    db: Session = Depends(get_db),
    user=Depends(require_slot_operator),
):
    return execute_transaction(
        db,
        lambda: report_task_delay(
            db, slot_id, data.delay_hours, data.reason,
            user.display_name or user.username,
        ),
    )

@router.post("/timeslots/{slot_id}/night-run", response_model=TimeSlotOut)
def night_run(
    slot_id: int,
    data: NightRunRequest,
    db: Session = Depends(get_db),
    user=Depends(require_slot_operator),
):
    slot = execute_transaction(
        db,
        lambda: record_night_run(
            db, slot_id, data.duration_hours, data.earliest_start, data.latest_end, user.id,
        ),
    )
    return _enrich_slot(slot, db)

@router.post("/generate")
def generate_schedule(
    data: ScheduleGenerateRequest,
    db: Session = Depends(get_db),
    user=Depends(require_management_user),
):
    scheduler = SchedulerService(db)
    result = scheduler.generate(data.project_ids, mode=data.mode)
    record_audit_log(
        db, user.display_name or user.username, "schedule_generated", "schedule",
        None, {"project_ids": data.project_ids or [], "mode": data.mode, "result": result.get("status")},
    )
    db.commit()
    return result

@router.post("/reschedule")
def reschedule(
    data: RescheduleRequest,
    db: Session = Depends(get_db),
    user=Depends(require_management_user),
):
    result = reschedule_service(db, data)
    record_audit_log(db, user.display_name or user.username, "schedule_rescheduled", "schedule", None, {"result": result.get("status")})
    db.commit()
    return result

@router.post("/insert-order", response_model=InsertOrderPreview)
def calculate_insert_cost(
    data: InsertOrderRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    try:
        return preview_insert(db, data)
    except ScheduleInsertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScheduleInsertInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/insert-order/confirm", response_model=InsertOrderResult)
def confirm_insert(
    data: InsertOrderRequest,
    db: Session = Depends(get_db),
    user=Depends(require_management_user),
):
    try:
        operator_name = user.display_name or user.username
        result = confirm_insert_service(db, data, operator_name=operator_name)
        record_audit_log(
            db,
            operator_name,
            "schedule_insert_confirmed",
            "schedule",
            None,
            result.audit_detail,
        )
        db.commit()
        return result
    except ScheduleInsertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScheduleInsertInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/daily-roll")
def daily_roll(
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    result = roll_schedule_tiers(db)
    result["approval_notifications"] = scan_approval_deadlines(db)
    return result

@router.get("/my-tasks", response_model=List[WorkspaceTaskOut])
def my_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    return get_workspace_tasks(db, user)


@router.get("/my-agenda", response_model=AgendaOut)
def my_agenda(
    start_date: date,
    end_date: date,
    assignee_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    try:
        return get_workspace_agenda(db, user, start_date, end_date, assignee_id)
    except WorkspaceAgendaPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except WorkspaceAgendaInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

def _empty_delay_fields() -> dict:
    return {
        "delay_hours": None,
        "delay_reason": None,
        "delay_reported_at": None,
        "delay_started_at": None,
    }

def _should_include_delay_fields(slot: TimeSlot) -> bool:
    if slot.status in {"blocked", "interrupted", "completed"}:
        return True
    return slot.status == "running" and slot.actual_start is not None

def _enrich_slot(slot: TimeSlot, db: Session) -> TimeSlotOut:
    task = db.query(Task).filter(Task.id == slot.task_id).first()
    inst = db.query(Instrument).filter(Instrument.id == slot.instrument_id).first()
    proj = db.query(Project).filter(Project.id == task.project_id).first() if task else None
    delay_fields = (
        _latest_delay_fields(task.id, db, slot)
        if task and (task.delay_status == "delayed" or _should_include_delay_fields(slot))
        else _empty_delay_fields()
    )
    actual_start, actual_end = _slot_actual_window(task, slot)
    return TimeSlotOut(
        id=slot.id, schedule_run_id=slot.schedule_run_id,
        task_id=slot.task_id, instrument_id=slot.instrument_id,
        plan_start=slot.plan_start, plan_end=slot.plan_end,
        actual_start=actual_start, actual_end=actual_end,
        tier=slot.tier, status=slot.status, execution_status=resolve_task_execution_status(task),
        task_name=task.name if task else None,
        task_type=task.task_type if task else None,
        task_status=task.status if task else None,
        delay_status=task.delay_status if task else "not_delayed",
        project_code=proj.code if proj else None,
        project_name=proj.name if proj else None,
        instrument_name=inst.name if inst else None,
        instrument_code=inst.code if inst else None,
        assignee_id=task.assignee_id if task else None,
        assignee_name=task.assignee.display_name if task and task.assignee else None,
        project_id=task.project_id if task else None,
        **delay_fields,
    )


def _slot_actual_window(task: Task | None, slot: TimeSlot):
    if slot.actual_start and slot.actual_end:
        return slot.actual_start, slot.actual_end
    if task:
        for segment in task.execution_segments:
            if segment.slot_id == slot.id and segment.started_at and segment.ended_at:
                return segment.started_at, segment.ended_at
    return slot.actual_start, slot.actual_end

def _latest_delay_fields(task_id: int, db: Session, slot: TimeSlot) -> dict:
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.action == "task_delay_reported")
        .order_by(AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    matched_logs: list[tuple[AuditLog, dict]] = []
    for log in logs:
        detail = _audit_detail_dict(log.detail)
        if detail.get("schedule_run_id") != slot.schedule_run_id:
            continue
        if detail.get("task_id") == task_id:
            matched_logs.append((log, detail))
    if not matched_logs:
        return _empty_delay_fields()
    total_hours = sum(float(detail.get("delay_hours") or 0) for _log, detail in matched_logs)
    reasons = [str(detail["reason"]) for _log, detail in reversed(matched_logs) if detail.get("reason")]
    delay_starts = [detail.get("delay_started_at") for _log, detail in matched_logs if detail.get("delay_started_at")]
    return {
        "delay_hours": total_hours,
        "delay_reason": "；".join(dict.fromkeys(reasons)) or None,
        "delay_reported_at": matched_logs[0][0].created_at,
        "delay_started_at": min(delay_starts) if delay_starts else None,
    }

def _audit_detail_dict(detail) -> dict:
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str):
        try:
            parsed = json.loads(detail)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
