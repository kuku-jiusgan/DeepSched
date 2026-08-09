from __future__ import annotations

from datetime import datetime, timedelta

from app.models import AuditLog, Project, Task, TaskDependency, TimeSlot, User
from app.schemas.approval_gate_schemas import (
    ApprovalGateActionOut,
    ApprovalGateCreate,
    ApprovalGateListOut,
    ApprovalGateOut,
    ApprovalGateSubmit,
    ApprovalGateTaskRef,
)
from app.schemas.schemas import ProjectPlanInsertConfirmRequest
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.project_access_service import FULL_PROJECT_ACCESS_ROLES, can_view_project
from app.services.push_notification_service import push_by_rule
from app.services.task_execution_service import (
    COMPLETED_TASK_STATUSES,
    TaskExecutionInvalidError,
    ensure_predecessors_completed,
)
from app.services.task_delay_status_service import reset_task_delay
from app.services.user_role_service import has_any_role, has_role


APPROVAL_WRITE_ROLES = FULL_PROJECT_ACCESS_ROLES - {"项目管理员"}
SYSTEM_ADMIN_ROLE = "系统管理员"
APPROVAL_WORKSPACE_ALL_VIEW_ROLES = {"系统管理员", "项目管理员"}
MOVABLE_SLOT_STATUSES = ["scheduled", "blocked"]


class ApprovalGateNotFoundError(Exception):
    pass


class ApprovalGatePermissionError(Exception):
    pass


class ApprovalGateInvalidError(Exception):
    pass


def create_approval_gate(db, project_id: int, data: ApprovalGateCreate, user: User) -> ApprovalGateOut:
    project = _project_or_404(db, project_id)
    _ensure_can_operate(project, user)
    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    task_by_id = {task.id: task for task in tasks}
    predecessor = task_by_id.get(data.predecessor_task_id)
    unlock_tasks = [task_by_id.get(task_id) for task_id in sorted(set(data.unlock_task_ids))]
    if not predecessor or any(task is None for task in unlock_tasks):
        raise ApprovalGateInvalidError("前置任务或解锁任务不属于当前项目")
    if predecessor.is_external_gate or any(task.is_external_gate for task in unlock_tasks):
        raise ApprovalGateInvalidError("方案签批只能连接普通项目任务")
    if predecessor.id in _downstream_ids(db, {task.id for task in unlock_tasks if task}):
        raise ApprovalGateInvalidError("方案签批会形成循环依赖")
    affected_ids = _downstream_ids(db, {task.id for task in unlock_tasks if task})
    affected_tasks = [task_by_id[task_id] for task_id in affected_ids if task_id in task_by_id]
    protected = [task for task in affected_tasks if task.schedule_lock_status != "none"]
    if protected:
        names = "、".join(task.name for task in protected[:3])
        raise ApprovalGateInvalidError(f"下游任务【{names}】已冻结、运行或完成，不能增加方案签批")

    gate = Task(
        project_id=project_id,
        name=data.name.strip() or "方案签批",
        task_type="approval_gate",
        requires_instrument=False,
        requires_human=False,
        est_duration_hours=None,
        switchover_hours=0,
        assignee_id=project.manager_id,
        status="waiting_external",
        is_external_gate=True,
        gate_status="not_submitted",
        schedule_dirty=False,
    )
    db.add(gate)
    db.flush()
    db.add(TaskDependency(task_id=gate.id, predecessor_id=predecessor.id))
    for task in unlock_tasks:
        db.add(TaskDependency(task_id=task.id, predecessor_id=gate.id))
    _clear_descendant_slots(db, affected_ids)
    for task in affected_tasks:
        task.status = "waiting_external"
        task.schedule_dirty = False
    _audit(db, user, "approval_gate_created", gate, {
        "predecessor_task_id": predecessor.id,
        "unlock_task_ids": [task.id for task in unlock_tasks],
    })
    _notify(db, gate, "approval_pending", "方案待提交客户", f"项目【{project.code}】已增加方案签批，请在方案完成后提交客户。")
    db.commit()
    db.refresh(gate)
    return _gate_out(db, gate, user)


def list_approval_gates(
    db,
    user: User,
    status: str | None = None,
    keyword: str | None = None,
    project_id: int | None = None,
    manager_id: int | None = None,
    risk: str | None = None,
    expected_from: datetime | None = None,
    expected_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    workspace_only: bool = False,
) -> ApprovalGateListOut:
    gates = db.query(Task).filter(Task.is_external_gate.is_(True)).order_by(
        Task.submitted_at.desc(), Task.id.desc()
    ).all()
    if workspace_only:
        visible = [
            gate for gate in gates
            if gate.project and (
                any(has_role(user, role) for role in APPROVAL_WORKSPACE_ALL_VIEW_ROLES)
                or gate.assignee_id == user.id
            )
        ]
    else:
        visible = [gate for gate in gates if gate.project and can_view_project(gate.project, user)]
    all_items = [_gate_out(db, gate, user) for gate in visible]
    pending_count = sum(item.gate_status != "approved" for item in all_items)
    approved_count = sum(item.gate_status == "approved" for item in all_items)
    upcoming_count = sum(item.risk_status == "upcoming" for item in all_items)
    overdue_count = sum(item.risk_status in {"overdue", "deadline_risk"} for item in all_items)

    items = all_items
    if status == "pending":
        items = [item for item in items if item.gate_status != "approved"]
    elif status == "approved":
        items = [item for item in items if item.gate_status == "approved"]
    if project_id:
        items = [item for item in items if item.project_id == project_id]
    if manager_id:
        items = [item for item in items if item.project_manager_id == manager_id]
    if risk:
        items = [item for item in items if item.risk_status == risk]
    if expected_from:
        expected_from = _naive_datetime(expected_from)
        items = [item for item in items if item.expected_approval_at and item.expected_approval_at >= expected_from]
    if expected_to:
        expected_to = _naive_datetime(expected_to)
        items = [item for item in items if item.expected_approval_at and item.expected_approval_at <= expected_to]
    if keyword:
        normalized = keyword.strip().lower()
        items = [item for item in items if normalized in " ".join(filter(None, [
            item.project_code, item.project_name, item.client_name, item.name,
        ])).lower()]

    total = len(items)
    start = (page - 1) * page_size
    return ApprovalGateListOut(
        items=items[start:start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        pending_count=pending_count,
        approved_count=approved_count,
        upcoming_count=upcoming_count,
        overdue_count=overdue_count,
    )


def get_approval_gate(db, gate_id: int, user: User) -> ApprovalGateOut:
    gate = _gate_or_404(db, gate_id)
    if gate.assignee_id != user.id and not can_view_project(gate.project, user):
        raise ApprovalGateNotFoundError("签批方案不存在或无权查看")
    return _gate_out(db, gate, user)


def submit_approval_gate(
    db,
    gate_id: int,
    data: ApprovalGateSubmit,
    user: User,
) -> ApprovalGateActionOut:
    gate = _gate_or_404(db, gate_id)
    _ensure_can_operate_gate(gate, user)
    if gate.gate_status == "approved":
        raise ApprovalGateInvalidError("该方案已经签批")
    expected_at = _naive_datetime(data.expected_approval_at)
    if expected_at <= datetime.now():
        raise ApprovalGateInvalidError("预计签批完成时间必须晚于当前时间")
    gate.gate_status = "waiting_approval"
    gate.status = "waiting_approval"
    gate.submitted_at = gate.submitted_at or datetime.now()
    gate.expected_approval_at = expected_at
    gate.approval_note = data.approval_note
    descendants = _descendant_tasks(db, gate.id)
    for task in descendants:
        if not task.is_external_gate and task.schedule_lock_status == "none":
            task.status = "pending"
            reset_task_delay(task)
            task.schedule_dirty = True
    _audit(db, user, "approval_gate_submitted", gate, {
        "expected_approval_at": expected_at.isoformat(),
    })
    db.commit()
    result = _apply_gate_schedule(db, gate, is_forecast=True)
    return ApprovalGateActionOut(
        gate=_gate_out(db, gate, user),
        schedule_status=gate.approval_schedule_status or result.status,
        schedule_message=result.message,
        preview_token=gate.approval_preview_token,
    )


def approve_approval_gate(db, gate_id: int, note: str | None, user: User) -> ApprovalGateActionOut:
    gate = _gate_or_404(db, gate_id)
    _ensure_can_operate_gate(gate, user)
    _ensure_gate_predecessors_completed(gate)
    if gate.gate_status not in {"not_submitted", "waiting_approval"}:
        raise ApprovalGateInvalidError("该方案已经完成签批")
    previous_status = gate.gate_status
    approved_at = datetime.now()
    gate.gate_status = "approved"
    gate.status = "done"
    gate.submitted_at = gate.submitted_at or approved_at
    gate.approved_at = approved_at
    gate.approved_by = user.id
    if note is not None:
        gate.approval_note = note
    for task in _descendant_tasks(db, gate.id):
        if not task.is_external_gate and task.schedule_lock_status == "none":
            task.status = "pending"
            reset_task_delay(task)
            task.schedule_dirty = True
    _audit(db, user, "approval_gate_approved", gate, {
        "approved_at": gate.approved_at.isoformat(),
        "direct_confirmation": previous_status == "not_submitted",
    })
    db.commit()
    result = _apply_gate_schedule(db, gate, is_forecast=False)
    return ApprovalGateActionOut(
        gate=_gate_out(db, gate, user),
        schedule_status=gate.approval_schedule_status or result.status,
        schedule_message=result.message,
        preview_token=gate.approval_preview_token,
    )


def confirm_approval_schedule(db, gate_id: int, preview_token: str, user: User) -> ApprovalGateActionOut:
    gate = _gate_or_404(db, gate_id)
    _ensure_can_operate_gate(gate, user)
    if gate.approval_schedule_status != "confirmation_required":
        raise ApprovalGateInvalidError("当前签批没有待确认的跨项目排程")
    from app.services.project_plan_apply_service import confirm_project_plan_insert

    result = confirm_project_plan_insert(db, ProjectPlanInsertConfirmRequest(
        project_id=gate.project_id,
        preview_token=preview_token,
    ))
    _store_schedule_result(db, gate, result, is_forecast=False)
    _audit(db, user, "approval_schedule_impact_confirmed", gate, {
        "schedule_run_id": result.schedule_run_id,
        "moved_tasks": result.moved_tasks,
    })
    db.commit()
    return ApprovalGateActionOut(
        gate=_gate_out(db, gate, user),
        schedule_status=gate.approval_schedule_status or "applied",
        schedule_message=result.message,
    )


def unapproved_gate_context(db, tasks: list[Task]) -> tuple[dict[int, datetime], set[int]]:
    task_ids = {task.id for task in tasks}
    if not task_ids:
        return {}, set()
    project_ids = {task.project_id for task in tasks}
    project_tasks = db.query(Task).filter(Task.project_id.in_(project_ids)).all()
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_({task.id for task in project_tasks})
    ).all()
    predecessors: dict[int, set[int]] = {}
    for dependency in dependencies:
        predecessors.setdefault(dependency.task_id, set()).add(dependency.predecessor_id)
    task_by_id = {task.id: task for task in project_tasks}
    bounds: dict[int, datetime] = {}
    forecast_ids: set[int] = set()
    for task_id in task_ids:
        for gate in _upstream_gates(task_id, predecessors, task_by_id):
            bound = gate.approved_at if gate.gate_status == "approved" else gate.expected_approval_at
            if bound and (task_id not in bounds or bound > bounds[task_id]):
                bounds[task_id] = bound
            if gate.gate_status != "approved":
                forecast_ids.add(task_id)
    return bounds, forecast_ids


def scan_approval_deadlines(db) -> int:
    now = datetime.now()
    gates = db.query(Task).filter(
        Task.is_external_gate.is_(True),
        Task.gate_status == "waiting_approval",
        Task.expected_approval_at.isnot(None),
    ).all()
    sent = 0
    for gate in gates:
        action = "approval_overdue_notified" if gate.expected_approval_at < now else "approval_upcoming_notified"
        if gate.expected_approval_at >= now + timedelta(days=2):
            continue
        if db.query(AuditLog.id).filter(AuditLog.action == action, AuditLog.target_id == gate.id).first():
            continue
        state = "已超过预计签批时间" if gate.expected_approval_at < now else "将在两天内到期"
        sent += _notify(
            db,
            gate,
            "approval_due",
            "方案签批时间提醒",
            f"项目【{gate.project.code}】的方案签批{state}，请关注客户反馈和结题风险。",
        )
        db.add(AuditLog(
            user_name="system",
            action=action,
            target_type="approval_gate",
            target_id=gate.id,
            detail={"expected_approval_at": gate.expected_approval_at.isoformat()},
        ))
    db.commit()
    return sent


def _apply_gate_schedule(db, gate: Task, is_forecast: bool):
    from app.services.project_plan_apply_service import apply_project_plan

    result = apply_project_plan(db, gate.project_id)
    gate = _gate_or_404(db, gate.id)
    _store_schedule_result(db, gate, result, is_forecast)
    db.commit()
    return result


def _store_schedule_result(db, gate: Task, result, is_forecast: bool) -> None:
    if result.status == "applied":
        gate.approval_schedule_status = "forecast" if is_forecast else "applied"
    elif result.status == "insert_confirmation_required":
        gate.approval_schedule_status = "confirmation_required"
    else:
        gate.approval_schedule_status = "deadline_risk"
    gate.approval_schedule_message = result.message
    gate.approval_preview_token = result.preview_token
    gate.approval_schedule_run_id = result.schedule_run_id
    gate.approval_moved_tasks = result.moved_tasks
    if not is_forecast:
        if gate.approval_schedule_status == "applied":
            content = f"项目【{gate.project.code}】客户方案已同意，后续验证排程已自动更新。"
        elif gate.approval_schedule_status == "confirmation_required":
            content = f"项目【{gate.project.code}】客户方案已同意，需要确认跨项目排程影响。"
        else:
            content = f"项目【{gate.project.code}】客户方案已同意，但当前无法在结题日期前完成。"
        _notify(db, gate, "approval_schedule_result", "签批后排程结果", content)


def _gate_out(db, gate: Task, user: User) -> ApprovalGateOut:
    project = gate.project
    predecessor_tasks = [
        ApprovalGateTaskRef(
            id=dependency.predecessor.id,
            name=dependency.predecessor.name,
            status=dependency.predecessor.status,
            completed_at=_task_completed_at(dependency.predecessor),
        )
        for dependency in gate.predecessors
    ]
    unlock_tasks = [
        ApprovalGateTaskRef(id=dependency.task.id, name=dependency.task.name)
        for dependency in db.query(TaskDependency).filter(
            TaskDependency.predecessor_id == gate.id
        ).all()
    ]
    latest_approval_at = _latest_approval_at(db, gate, unlock_tasks)
    expected = gate.expected_approval_at
    risk_status = "normal"
    now = datetime.now()
    if gate.gate_status != "approved" and expected and expected < now:
        risk_status = "overdue"
    elif gate.gate_status != "approved" and latest_approval_at and expected and expected > latest_approval_at:
        risk_status = "deadline_risk"
    elif gate.gate_status != "approved" and expected and expected <= now + timedelta(days=2):
        risk_status = "upcoming"
    project_slots = [
        slot for task in project.tasks for slot in task.time_slots
        if slot.status in {"scheduled", "running", "paused", "blocked", "interrupted", "completed"}
    ]
    expected_completion = max((slot.plan_end for slot in project_slots), default=None)
    return ApprovalGateOut(
        id=gate.id,
        project_id=project.id,
        project_code=project.code,
        project_name=project.name,
        client_name=project.client_name,
        project_manager_id=project.manager_id,
        project_manager_name=project.manager.display_name if project.manager else None,
        assignee_id=gate.assignee_id,
        assignee_name=gate.assignee.display_name if gate.assignee else None,
        project_end_date=project.end_date,
        name=gate.name,
        gate_status=gate.gate_status or "not_submitted",
        expected_approval_at=gate.expected_approval_at,
        submitted_at=gate.submitted_at,
        approved_at=gate.approved_at,
        approved_by_name=gate.approved_by_user.display_name if gate.approved_by_user else None,
        approval_note=gate.approval_note,
        predecessor_tasks=predecessor_tasks,
        unlock_tasks=unlock_tasks,
        latest_approval_at=latest_approval_at,
        risk_status=risk_status,
        schedule_status=gate.approval_schedule_status,
        schedule_message=gate.approval_schedule_message,
        schedule_run_id=gate.approval_schedule_run_id,
        preview_token=gate.approval_preview_token,
        moved_tasks=gate.approval_moved_tasks or 0,
        project_expected_completion=expected_completion,
        can_operate=(
            _can_operate(project, user) or gate.assignee_id == user.id
        ) and not has_role(user, "项目管理员"),
    )


def _latest_approval_at(db, gate: Task, unlock_tasks: list[ApprovalGateTaskRef]) -> datetime | None:
    if not gate.project.end_date:
        return None
    project_tasks = {task.id: task for task in gate.project.tasks if not task.is_external_gate}
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(set(project_tasks))
    ).all()
    downstream: dict[int, list[int]] = {}
    for dependency in dependencies:
        downstream.setdefault(dependency.predecessor_id, []).append(dependency.task_id)

    def critical_hours(task_id: int, visiting: set[int]) -> float:
        if task_id in visiting or task_id not in project_tasks:
            return 0
        task = project_tasks[task_id]
        own = float(task.est_duration_hours or 0) + float(task.switchover_hours or 0)
        child_hours = [critical_hours(child_id, visiting | {task_id}) for child_id in downstream.get(task_id, [])]
        return own + max(child_hours, default=0)

    hours = max((critical_hours(task.id, set()) for task in unlock_tasks), default=0)
    return gate.project.end_date - timedelta(days=hours / 8)


def _clear_descendant_slots(db, task_ids: set[int]) -> None:
    if not task_ids:
        return
    delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
        TimeSlot.actual_start.is_(None),
    ), synchronize_session=False)


def _downstream_ids(db, seed_ids: set[int]) -> set[int]:
    dependencies = db.query(TaskDependency).all()
    by_predecessor: dict[int, set[int]] = {}
    for dependency in dependencies:
        by_predecessor.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)
    result = set(seed_ids)
    pending = list(seed_ids)
    while pending:
        predecessor_id = pending.pop()
        for task_id in by_predecessor.get(predecessor_id, set()):
            if task_id not in result:
                result.add(task_id)
                pending.append(task_id)
    return result


def _descendant_tasks(db, gate_id: int) -> list[Task]:
    task_ids = _downstream_ids(db, {gate_id}) - {gate_id}
    return db.query(Task).filter(Task.id.in_(task_ids)).all() if task_ids else []


def _upstream_gates(
    task_id: int,
    predecessors: dict[int, set[int]],
    task_by_id: dict[int, Task],
) -> list[Task]:
    result: list[Task] = []
    pending = list(predecessors.get(task_id, set()))
    visited: set[int] = set()
    while pending:
        predecessor_id = pending.pop()
        if predecessor_id in visited:
            continue
        visited.add(predecessor_id)
        predecessor = task_by_id.get(predecessor_id)
        if predecessor and predecessor.is_external_gate:
            result.append(predecessor)
        pending.extend(predecessors.get(predecessor_id, set()))
    return result


def _project_or_404(db, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ApprovalGateNotFoundError("项目不存在")
    return project


def _gate_or_404(db, gate_id: int) -> Task:
    gate = db.query(Task).filter(Task.id == gate_id, Task.is_external_gate.is_(True)).first()
    if not gate:
        raise ApprovalGateNotFoundError("签批方案不存在")
    return gate


def _can_operate(project: Project, user: User) -> bool:
    return has_any_role(user, APPROVAL_WRITE_ROLES) or project.manager_id == user.id


def _ensure_can_operate(project: Project, user: User) -> None:
    if not _can_operate(project, user):
        raise ApprovalGatePermissionError("无权操作该项目的方案签批")


def _ensure_can_operate_gate(gate: Task, user: User) -> None:
    if gate.assignee_id != user.id and not _can_operate(gate.project, user):
        raise ApprovalGatePermissionError("无权操作该方案签批任务")


def _ensure_gate_predecessors_completed(gate: Task) -> None:
    try:
        ensure_predecessors_completed(gate)
    except TaskExecutionInvalidError as exc:
        raise ApprovalGateInvalidError(str(exc))


def _task_completed_at(task: Task) -> datetime | None:
    if task.status not in COMPLETED_TASK_STATUSES:
        return None
    actual_ends = [slot.actual_end for slot in task.time_slots if slot.actual_end]
    return max(actual_ends, default=task.updated_at)


def _audit(db, user: User, action: str, gate: Task, detail: dict) -> None:
    db.add(AuditLog(
        user_name=user.display_name or user.username,
        action=action,
        target_type="approval_gate",
        target_id=gate.id,
        detail={"project_id": gate.project_id, **detail},
    ))


def _notify(db, gate: Task, rule_type: str, title: str, content: str) -> int:
    users = db.query(User).filter(User.is_active.is_(True)).all()
    recipients = [
        user for user in users
        if user.id == gate.assignee_id or has_any_role(user, APPROVAL_WRITE_ROLES)
    ]
    return push_by_rule(
        db,
        rule_type,
        recipients,
        title,
        content,
        related_entity_type="approval_gate",
        related_entity_id=gate.id,
        context_roles=["任务负责人"],
    )


def _naive_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is None else value.replace(tzinfo=None)
