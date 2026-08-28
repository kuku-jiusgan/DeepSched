"""方案签批的列表查询：可见性过滤、条件筛选与分页。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload, selectinload

from app.models import Project, Task, TaskDependency, User
from app.schemas.approval_gate_schemas import ApprovalGateListOut, ApprovalGateOut
from app.services.approval_gate_access_service import (
    APPROVAL_WORKSPACE_ALL_VIEW_ROLES,
    gate_or_404,
    is_project_member,
)
from app.services.approval_gate_errors import ApprovalGateNotFoundError
from app.services.approval_gate_presentation_service import gate_out, naive_datetime
from app.services.project_access_service import FULL_PROJECT_ACCESS_ROLES, can_view_project
from app.services.user_role_service import has_any_role, has_role


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
    gates_query = db.query(Task).options(
        selectinload(Task.project).selectinload(Project.manager),
        joinedload(Task.assignee),
        joinedload(Task.approved_by_user),
        selectinload(Task.project).selectinload(Project.tasks).selectinload(Task.time_slots),
        selectinload(Task.project).selectinload(Project.tasks).selectinload(Task.assignee),
        selectinload(Task.predecessors).joinedload(TaskDependency.predecessor),
    ).filter(Task.is_external_gate.is_(True), Task.project_id.isnot(None))
    if workspace_only:
        if not has_any_role(user, APPROVAL_WORKSPACE_ALL_VIEW_ROLES):
            gates_query = gates_query.filter(or_(
                Task.assignee_id == user.id,
                Task.project.has(Project.manager_id == user.id),
                Task.project.has(Project.tasks.any(and_(
                    Task.assignee_id == user.id,
                    Task.is_external_gate.is_(False),
                ))),
            ))
    elif not has_any_role(user, FULL_PROJECT_ACCESS_ROLES):
        gates_query = gates_query.filter(or_(
            Task.project.has(Project.manager_id == user.id),
            Task.project.has(Project.tasks.any(Task.assignee_id == user.id)),
        ))
    if status == "pending":
        gates_query = gates_query.filter(Task.gate_status != "approved")
    elif status == "approved":
        gates_query = gates_query.filter(Task.gate_status == "approved")
    if project_id:
        gates_query = gates_query.filter(Task.project_id == project_id)
    if manager_id:
        gates_query = gates_query.filter(Task.project.has(Project.manager_id == manager_id))
    if expected_from:
        gates_query = gates_query.filter(Task.expected_approval_at >= naive_datetime(expected_from))
    if expected_to:
        gates_query = gates_query.filter(Task.expected_approval_at <= naive_datetime(expected_to))
    if keyword:
        normalized = f"%{keyword.strip()}%"
        gates_query = gates_query.filter(or_(
            Task.name.ilike(normalized),
            Task.project.has(or_(Project.code.ilike(normalized), Project.name.ilike(normalized), Project.client_name.ilike(normalized))),
        ))
    gates = gates_query.order_by(
        Task.submitted_at.desc(), Task.id.desc()
    ).all()
    if workspace_only:
        visible = [
            gate for gate in gates
            if gate.project and (
                any(has_role(user, role) for role in APPROVAL_WORKSPACE_ALL_VIEW_ROLES)
                or gate.assignee_id == user.id
                or is_project_member(gate.project, user)
            )
        ]
    else:
        visible = [gate for gate in gates if gate.project and can_view_project(gate.project, user)]
    all_items = [gate_out(db, gate, user) for gate in visible]
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
        expected_from = naive_datetime(expected_from)
        items = [item for item in items if item.expected_approval_at and item.expected_approval_at >= expected_from]
    if expected_to:
        expected_to = naive_datetime(expected_to)
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
    gate = gate_or_404(db, gate_id)
    if gate.assignee_id != user.id and not can_view_project(gate.project, user):
        raise ApprovalGateNotFoundError("签批方案不存在或无权查看")
    return gate_out(db, gate, user)
