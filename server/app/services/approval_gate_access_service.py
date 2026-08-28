"""方案签批的实体查找与操作权限判定。"""

from __future__ import annotations

from app.models import Project, Task, User
from app.services.approval_gate_errors import (
    ApprovalGateInvalidError,
    ApprovalGateNotFoundError,
    ApprovalGatePermissionError,
)
from app.services.project_access_service import FULL_PROJECT_ACCESS_ROLES
from app.services.user_role_service import has_any_role


APPROVAL_WRITE_ROLES = FULL_PROJECT_ACCESS_ROLES - {"项目管理员"}
APPROVAL_WORKSPACE_ALL_VIEW_ROLES = {"系统管理员", "项目管理员"}


def project_or_404(db, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ApprovalGateNotFoundError("项目不存在")
    return project

def gate_or_404(db, gate_id: int) -> Task:
    gate = db.query(Task).filter(Task.id == gate_id, Task.is_external_gate.is_(True)).first()
    if not gate:
        raise ApprovalGateNotFoundError("签批方案不存在")
    return gate

def can_operate_project(project: Project, user: User) -> bool:
    return has_any_role(user, APPROVAL_WRITE_ROLES) or project.manager_id == user.id

def resolve_gate_assignee_id(db, project: Project, assignee_id: int | None) -> int | None:
    if assignee_id is None:
        return project.manager_id
    assignee = db.query(User.id).filter(
        User.id == assignee_id,
        User.is_active.is_(True),
    ).first()
    if not assignee:
        raise ApprovalGateInvalidError("方案签批负责人不存在或已停用")
    return assignee_id

def is_project_member(project: Project, user: User) -> bool:
    return project.manager_id == user.id or any(
        task.assignee_id == user.id
        for task in project.tasks
        if not task.is_external_gate
    )

def can_operate_gate_task(gate: Task, user: User) -> bool:
    return (
        has_any_role(user, APPROVAL_WRITE_ROLES)
        or gate.assignee_id == user.id
        or is_project_member(gate.project, user)
    )

def ensure_can_operate_project(project: Project, user: User) -> None:
    if not can_operate_project(project, user):
        raise ApprovalGatePermissionError("无权操作该项目的方案签批")

def ensure_can_operate_gate(gate: Task, user: User) -> None:
    if not can_operate_gate_task(gate, user):
        raise ApprovalGatePermissionError("无权操作该方案签批任务")
