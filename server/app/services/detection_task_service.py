from app.models import Project, Task, TimeSlot
from sqlalchemy.orm import selectinload
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.project_date_service import normalize_project_end, normalize_project_start
from app.services.project_reference_validation_service import (
    ProjectReferenceInvalidError,
    validate_task_references,
)
from app.schemas.schemas import ProjectPlanInsertConfirmRequest
from app.services.project_plan_apply_service import (
    ProjectPlanInvalidError,
    apply_project_plan,
    confirm_project_plan_insert,
)
from app.services.user_role_service import has_role
from app.services.audit_log_service import record_audit_log


class DetectionTaskInvalidError(Exception):
    pass


class DetectionTaskNotFoundError(Exception):
    pass


SYSTEM_ADMIN_ROLE = "系统管理员"
FULL_DETECTION_TASK_ACCESS_ROLES = {SYSTEM_ADMIN_ROLE, "分析所所长", "技术组长"}
FULL_DETECTION_TASK_VIEW_ROLES = FULL_DETECTION_TASK_ACCESS_ROLES | {"项目管理员"}


def list_detection_tasks(db, user) -> list[Project]:
    query = db.query(Project).options(
        selectinload(Project.tasks).selectinload(Task.assignee),
        selectinload(Project.tasks).selectinload(Task.time_slots),
        selectinload(Project.manager),
    ).filter(Project.project_kind == "detection")
    if not any(has_role(user, role) for role in FULL_DETECTION_TASK_VIEW_ROLES):
        query = query.filter(Project.tasks.any(Task.assignee_id == user.id))
    return query.order_by(Project.created_at.desc()).all()


def create_detection_task(db, data, user=None) -> tuple[Project, dict]:
    if not data.assignee_id:
        raise DetectionTaskInvalidError("检测任务必须指定执行人")
    code = data.code.strip()
    name = data.name.strip()
    if not code or not name:
        raise DetectionTaskInvalidError("检测任务编号和名称不能为空")
    if db.query(Project).filter(Project.code == code).first():
        raise DetectionTaskInvalidError(f"编号 {code} 已存在")
    if data.end_date < data.start_date:
        raise DetectionTaskInvalidError("计划完成时间不能早于计划开始时间")
    project = Project(
        code=code, name=name, client_name=data.client_name,
        estimated_hours=data.est_duration_hours, priority=data.priority,
        manager_id=data.manager_id, project_kind="detection", status="pending",
        start_date=normalize_project_start(data.start_date),
        end_date=normalize_project_end(data.end_date),
    )
    db.add(project)
    db.flush()
    try:
        validate_task_references(
            db, project.id, parent_id=None, milestone_id=None, predecessor_ids=[],
            assignee_id=data.assignee_id,
            instrument_ids=data.instrument_ids,
        )
    except ProjectReferenceInvalidError as exc:
        db.rollback()
        raise DetectionTaskInvalidError(str(exc)) from exc
    task = Task(
        project_id=project.id, name=name, task_type=data.task_type,
        requires_instrument=data.requires_instrument,
        requires_human=data.requires_human,
        est_duration_hours=data.est_duration_hours,
        switchover_hours=data.switchover_hours,
        allow_split=data.allow_split, allow_transfer=data.allow_transfer,
        priority_weight=1.0, assignee_id=data.assignee_id,
        instrument_ids=data.instrument_ids, parent_id=None,
    )
    db.add(task)
    db.commit()
    db.refresh(project)
    result = _apply_detection_plan(db, project.id)
    db.refresh(project)
    _record_detection_audit(db, user, "task_created", project, task)
    db.commit()
    return project, result


def update_detection_task(db, detection_id: int, data, user) -> tuple[Project, dict]:
    project = _get_detection_task(db, detection_id, user)
    if not data.assignee_id:
        raise DetectionTaskInvalidError("检测任务必须指定执行人")
    task = project.tasks[0]
    lock_status = task.schedule_lock_status
    if lock_status == "completed":
        raise DetectionTaskInvalidError("已完成的检测任务不能编辑")
    code = data.code.strip()
    name = data.name.strip()
    if not code or not name:
        raise DetectionTaskInvalidError("检测任务编号和名称不能为空")
    duplicate = db.query(Project).filter(Project.code == code, Project.id != detection_id).first()
    if duplicate:
        raise DetectionTaskInvalidError(f"编号 {code} 已存在")
    if data.end_date < data.start_date:
        raise DetectionTaskInvalidError("计划完成时间不能早于计划开始时间")
    if lock_status != "none":
        return _update_locked_detection_task(db, project, task, data, code, name)
    try:
        validate_task_references(
            db, project.id, parent_id=None, milestone_id=None, predecessor_ids=[],
            assignee_id=data.assignee_id, instrument_ids=data.instrument_ids,
        )
    except ProjectReferenceInvalidError as exc:
        raise DetectionTaskInvalidError(str(exc)) from exc
    delete_time_slots_and_refresh(
        db,
        db.query(TimeSlot).filter(TimeSlot.task_id == task.id),
    )
    project.code, project.name = code, name
    project.client_name, project.priority = data.client_name, data.priority
    project.manager_id, project.estimated_hours = data.manager_id, data.est_duration_hours
    project.start_date = normalize_project_start(data.start_date)
    project.end_date = normalize_project_end(data.end_date)
    task.name, task.task_type = name, data.task_type
    task.requires_instrument, task.requires_human = data.requires_instrument, data.requires_human
    task.est_duration_hours, task.switchover_hours = data.est_duration_hours, data.switchover_hours
    task.allow_split, task.allow_transfer = data.allow_split, data.allow_transfer
    task.instrument_ids, task.assignee_id = data.instrument_ids, data.assignee_id
    task.status, task.schedule_dirty = "pending", False
    db.commit()
    result = _apply_detection_plan(db, project.id)
    db.refresh(project)
    _record_detection_audit(db, user, "task_updated", project, task)
    db.commit()
    return project, result


def _update_locked_detection_task(db, project: Project, task: Task, data, code: str, name: str) -> tuple[Project, dict]:
    _ensure_locked_update_only_changes_safe_fields(project, task, data, code)
    latest_slot_end = max((slot.plan_end for slot in task.time_slots), default=None)
    normalized_end = normalize_project_end(data.end_date)
    if latest_slot_end and normalized_end < latest_slot_end:
        raise DetectionTaskInvalidError("计划完成时间不能早于已有排程结束时间")
    project.name = name
    project.end_date = normalized_end
    task.name = name
    db.commit()
    db.refresh(project)
    _record_detection_audit(db, user, "task_updated", project, task)
    db.commit()
    return project, {"status": "ok", "message": "检测任务已更新"}


def _ensure_locked_update_only_changes_safe_fields(project: Project, task: Task, data, code: str) -> None:
    normalized_start = normalize_project_start(data.start_date)
    if code != project.code:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改任务编号")
    if normalized_start != project.start_date:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改计划开始时间")
    if data.client_name != project.client_name:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改客户名称")
    if data.priority != project.priority:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改优先级")
    if data.manager_id != project.manager_id:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改项目负责人")
    if data.task_type != task.task_type:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改任务类型")
    if data.est_duration_hours != task.est_duration_hours:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改预计耗时")
    if data.switchover_hours != task.switchover_hours:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改切换时间")
    if data.requires_instrument != task.requires_instrument or data.requires_human != task.requires_human:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改资源类型")
    if data.allow_split != task.allow_split or data.allow_transfer != task.allow_transfer:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改拆分或转移规则")
    if sorted(data.instrument_ids) != sorted(task.instrument_ids or []):
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改指定仪器")
    if data.assignee_id != task.assignee_id:
        raise DetectionTaskInvalidError("运行中或冻结期检测任务不能修改执行人")


def confirm_detection_task_insert(
    db,
    detection_id: int,
    preview_token: str,
    user,
) -> tuple[Project, dict]:
    project = _get_detection_task(db, detection_id, user)
    try:
        result = confirm_project_plan_insert(
            db,
            ProjectPlanInsertConfirmRequest(
                project_id=project.id,
                preview_token=preview_token,
            ),
        )
    except ProjectPlanInvalidError as exc:
        raise DetectionTaskInvalidError(str(exc)) from exc
    db.refresh(project)
    return project, result.model_dump()


def _apply_detection_plan(db, project_id: int) -> dict:
    try:
        return apply_project_plan(db, project_id).model_dump()
    except ProjectPlanInvalidError as exc:
        raise DetectionTaskInvalidError(str(exc)) from exc


def delete_detection_task(db, detection_id: int, user) -> None:
    project = _get_detection_task(db, detection_id, user)
    if any(task.status in {"done", "completed"} for task in project.tasks) and not has_role(user, SYSTEM_ADMIN_ROLE):
        raise DetectionTaskInvalidError("已完成检测任务不允许删除")
    task_ids = [task.id for task in project.tasks]
    if task_ids:
        delete_time_slots_and_refresh(
            db,
            db.query(TimeSlot).filter(TimeSlot.task_id.in_(task_ids)),
        )
    _record_detection_audit(db, user, "task_deleted", project, project.tasks[0] if project.tasks else None)
    db.delete(project)
    db.commit()


def _record_detection_audit(db, user, action: str, project: Project, task: Task | None) -> None:
    if not user:
        return
    target = " · ".join(part for part in [project.code, project.name, task.name if task else None] if part)
    record_audit_log(db, user.display_name or user.username, action, "task", task.id if task else project.id, {
        "category": "task",
        "summary": {"task_created": "新增检测任务", "task_updated": "修改检测任务", "task_deleted": "删除检测任务"}[action],
        "target_display": target,
        "result": "success",
    })


def _get_detection_task(db, detection_id: int, user=None) -> Project:
    project = db.query(Project).filter(
        Project.id == detection_id,
        Project.project_kind == "detection",
    ).first()
    if project is None or (user is not None and not _can_view_detection_task(project, user)):
        raise DetectionTaskNotFoundError("检测任务不存在")
    return project


def _can_view_detection_task(project: Project, user) -> bool:
    return (
        any(has_role(user, role) for role in FULL_DETECTION_TASK_ACCESS_ROLES)
        or any(task.assignee_id == user.id for task in project.tasks)
    )
