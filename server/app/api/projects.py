from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.core.database import get_db
from app.api.users import auth_token, get_current_user
from app.models import Project, Milestone, Task, TaskDependency, TaskCapabilityRequirement, TimeSlot
from app.schemas.schemas import (
    ProjectCreate, ProjectOut, TaskCreate, TaskUpdate, TaskReorder, TaskOut,
    MilestoneCreate, MilestoneOut, TaskCapabilityReqOut
)
from app.services.project_access_service import (
    ProjectNotVisibleError,
    get_visible_project,
    get_visible_project_dag,
    list_visible_projects,
)
from app.services.project_hours_validation_service import (
    ProjectHoursExceededError,
    validate_project_estimated_hours,
)
from app.services.project_task_rollup_service import recalculate_project_parent_hours
from app.services.project_service import ProjectCodeExistsError, create_project as create_project_service
from app.services.project_plan_change_service import (
    PlanChangeInvalidError,
    PlanChangeNotFoundError,
    delete_task_plan,
    update_project_plan,
    update_task_plan,
)
from app.services.project_status_service import calculate_project_status
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.api.access import (
    require_project_editor_by_proj_id,
    require_task_editor,
)
from app.services.project_reference_validation_service import (
    ProjectReferenceInvalidError,
    validate_task_references,
)
from app.services.audit_log_service import (
    project_audit_detail,
    project_audit_snapshot,
    record_audit_log,
    task_audit_detail,
)
from app.services.user_role_service import has_role
from app.services.task_reorder_service import reorder_project_tasks, TaskReorderInvalidError
from app.services.project_actual_hours_service import project_actual_hours_map
from app.services.project_health_service import get_project_health
from app.schemas.project_health_schemas import ProjectHealthOut

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

PROJECT_INFO_WRITE_ROLES = {"系统管理员", "项目管理员", "分析所所长", "技术组长"}

@router.post("", response_model=ProjectOut)
def create_project(data: ProjectCreate, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    user = ensure_project_info_write_permission(token, db)
    try:
        project = create_project_service(db, data)
        record_audit_log(
            db, user.display_name or user.username, "project_created", "project", project.id,
            project_audit_detail(project),
        )
        db.commit()
        return project_response(project, db)
    except ProjectCodeExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.get("", response_model=List[ProjectOut])
def list_projects(
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    projects = list_visible_projects(db, get_current_user(token, db))
    actual_hours = project_actual_hours_map(db, projects)
    return [project_response(project, db, actual_hours.get(project.id, 0)) for project in projects]

@router.get("/{proj_id}", response_model=ProjectOut)
def get_project(
    proj_id: int,
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    try:
        return project_response(get_visible_project(db, proj_id, get_current_user(token, db)), db)
    except ProjectNotVisibleError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{proj_id}/health", response_model=ProjectHealthOut)
def get_project_health_summary(
    proj_id: int,
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    try:
        project = get_visible_project(db, proj_id, get_current_user(token, db))
        return get_project_health(db, project)
    except ProjectNotVisibleError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

@router.put("/{proj_id}", response_model=ProjectOut)
def update_project(proj_id: int, data: ProjectCreate, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    user = ensure_project_info_write_permission(token, db)
    try:
        existing = db.query(Project).filter(Project.id == proj_id).first()
        before = project_audit_snapshot(existing) if existing else None
        project = update_project_plan(db, proj_id, data)
        record_audit_log(
            db, user.display_name or user.username, "project_updated", "project", project.id,
            project_audit_detail(project, before),
        )
        db.commit()
        return project_response(project, db)
    except PlanChangeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PlanChangeInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

def ensure_project_info_write_permission(token: str, db: Session):
    return get_current_user(token, db)

def project_response(project: Project, db: Session, actual_hours: float | None = None) -> dict:
    data = ProjectOut.model_validate(project).model_dump()
    data["status"] = calculate_project_status(project)
    data["delivery_status"] = get_project_health(db, project).summary.delivery_status
    data["actual_hours"] = actual_hours if actual_hours is not None else project_actual_hours_map(db, [project]).get(project.id, 0)
    return data

@router.delete("/{proj_id}")
def delete_project(
    proj_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_project_editor_by_proj_id),
):
    proj = db.query(Project).filter(Project.id == proj_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
    if calculate_project_status(proj) == "completed":
        raise HTTPException(status_code=409, detail="已完成项目不允许删除")
    # Delete related records
    task_ids = [t.id for t in db.query(Task).filter(Task.project_id == proj_id).all()]
    for tid in task_ids:
        db.query(TaskDependency).filter(
            (TaskDependency.predecessor_id == tid) | (TaskDependency.task_id == tid)
        ).delete()
        db.query(TaskCapabilityRequirement).filter(TaskCapabilityRequirement.task_id == tid).delete()
    if task_ids:
        delete_time_slots_and_refresh(
            db,
            db.query(TimeSlot).filter(TimeSlot.task_id.in_(task_ids)),
        )
    db.query(Task).filter(Task.project_id == proj_id).delete()
    db.query(Milestone).filter(Milestone.project_id == proj_id).delete()
    record_audit_log(
        db,
        user.display_name or user.username,
        "project_deleted",
        "project",
        proj.id,
        {"project_code": proj.code, "project_name": proj.name, "task_count": len(task_ids)},
    )
    db.delete(proj)
    db.commit()
    return {"detail": "已删除"}

@router.post("/{proj_id}/milestones", response_model=MilestoneOut)
def add_milestone(
    proj_id: int,
    data: MilestoneCreate,
    db: Session = Depends(get_db),
    _user=Depends(require_project_editor_by_proj_id),
):
    ms = Milestone(project_id=proj_id, name=data.name, due_date=data.due_date)
    db.add(ms)
    db.commit()
    db.refresh(ms)
    return ms

@router.post("/{proj_id}/tasks", response_model=TaskOut)
def add_task(
    proj_id: int,
    data: TaskCreate,
    db: Session = Depends(get_db),
    user=Depends(require_project_editor_by_proj_id),
):
    try:
        validate_task_references(
            db,
            proj_id,
            parent_id=data.parent_id,
            milestone_id=data.milestone_id,
            predecessor_ids=data.predecessor_ids,
            assignee_id=data.assignee_id,
            instrument_ids=data.instrument_ids,
        )
    except ProjectReferenceInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    task = Task(
        project_id=proj_id, name=data.name, task_type=data.task_type,
        requires_instrument=data.requires_instrument, requires_human=data.requires_human,
        est_duration_hours=data.est_duration_hours, switchover_hours=data.switchover_hours,
        assignee_id=data.assignee_id, parent_id=data.parent_id,
        allow_split=data.allow_split, allow_transfer=data.allow_transfer,
        milestone_id=data.milestone_id, priority_weight=data.priority_weight
    )
    task.instrument_ids = data.instrument_ids
    db.add(task)
    db.flush()
    # Dependencies
    for pred_id in data.predecessor_ids:
        db.add(TaskDependency(task_id=task.id, predecessor_id=pred_id))
    recalculate_project_parent_hours(db, proj_id)
    try:
        validate_project_estimated_hours(db, proj_id)
    except ProjectHoursExceededError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    record_audit_log(
        db,
        user.display_name or user.username,
        "task_created",
        "task",
        task.id,
        task_audit_detail(db, task, "新增任务"),
    )
    db.commit()
    db.refresh(task)
    return _task_to_out(task, db)

@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_task_editor),
):
    try:
        delete_task_plan(
            db,
            task_id,
            allow_completed=has_role(user, "系统管理员"),
            actor_name=user.display_name or user.username,
        )
        return {"detail": "已删除"}
    except PlanChangeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PlanChangeInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.put("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    data: TaskUpdate,
    db: Session = Depends(get_db),
    user=Depends(require_task_editor),
):
    try:
        return _task_to_out(
            update_task_plan(db, task_id, data, user.display_name or user.username),
            db,
        )
    except PlanChangeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PlanChangeInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post("/{proj_id}/tasks/reorder", response_model=dict)
def reorder_tasks(
    proj_id: int, data: TaskReorder, db: Session = Depends(get_db),
    user=Depends(require_project_editor_by_proj_id),
):
    try:
        reorder_project_tasks(
            db,
            proj_id,
            data.parent_id,
            data.task_ids,
            user.display_name or user.username,
        )
        return {"detail": "任务顺序已保存"}
    except TaskReorderInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
@router.get("/{proj_id}/dag")
def get_project_dag(
    proj_id: int,
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    try:
        return get_visible_project_dag(db, proj_id, get_current_user(token, db))
    except ProjectNotVisibleError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
def _task_to_out(task: Task, db: Session) -> TaskOut:
    preds = [d.predecessor_id for d in task.predecessors]
    children_out = [_task_to_out(c, db) for c in task.children] if task.children else []
    return TaskOut(
        id=task.id, project_id=task.project_id, name=task.name,
        task_type=task.task_type, requires_instrument=task.requires_instrument,
        requires_human=task.requires_human, est_duration_hours=task.est_duration_hours,
        switchover_hours=task.switchover_hours, allow_split=task.allow_split, status=task.status,
        schedule_dirty=bool(task.schedule_dirty), schedule_lock_status=task.schedule_lock_status,
        can_edit_schedule_fields=task.can_edit_schedule_fields,
        earliest_start=task.earliest_start, latest_due=task.latest_due,
        priority_weight=task.priority_weight,
        instrument_ids=task.instrument_ids or [], predecessor_ids=preds,
        assignee_id=task.assignee_id, parent_id=task.parent_id, plan_order=task.plan_order or 0,
        assignee_name=task.assignee.display_name if task.assignee else None,
        is_external_gate=bool(task.is_external_gate), gate_status=task.gate_status,
        expected_approval_at=task.expected_approval_at, submitted_at=task.submitted_at,
        approved_at=task.approved_at,
        approved_by_name=task.approved_by_user.display_name if task.approved_by_user else None,
        approval_note=task.approval_note,
        approval_schedule_status=task.approval_schedule_status,
        children=children_out
    )
