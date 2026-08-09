from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.users import require_authenticated_user
from app.core.database import get_db
from app.models import User
from app.services.access_control_service import (
    AccessDeniedError,
    AccessResourceNotFoundError,
    require_management_user as ensure_management_user,
    require_notification_owner as ensure_notification_owner,
    require_project_editor as ensure_project_editor,
    require_slot_operator as ensure_slot_operator,
    require_task_editor as ensure_task_editor,
)
from app.services.role_permission_service import action_allowed_for_roles
from app.services.user_role_service import user_roles


def require_configured_operation(
    request: Request,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    permission_target = _operation_permission(request.method, request.url.path)
    if permission_target and not action_allowed_for_roles(db, user_roles(user), *permission_target):
        raise HTTPException(status_code=403, detail="当前角色没有执行该操作的按钮权限")


def _operation_permission(method: str, path: str) -> tuple[str, str] | None:
    if path.startswith("/api/v1/role-permissions"):
        return "/system/roles", "save"
    if path.startswith("/api/v1/detection-tasks"):
        action = "create" if method == "POST" else "edit" if method == "PUT" else "delete"
        return "/projects/detection-tasks", action
    if path.startswith("/api/v1/task-types"):
        action = "create" if method == "POST" else "delete" if method == "DELETE" else "toggle" if "/toggle" in path else "edit"
        return "/system/basic", action
    if path.startswith("/api/v1/alert-rules"):
        return "/system/alerts", "edit_channel" if "push-config" in path else "edit_rule"
    if path.startswith("/api/v1/schedule-rules"):
        return "/schedule/rules", "toggle" if "/toggle" in path else "edit"
    if path.startswith("/api/v1/approval-gates"):
        action = "confirm_impact" if "confirm" in path else "approve" if "approve" in path else "submit"
        return "/tasks/workspace", action
    if path.startswith("/api/v1/instruments/faults") or "/fault" in path:
        return "/tasks/faults", "resolve" if "resolve" in path else "create"
    if path.startswith("/api/v1/instruments"):
        if "/capabilities" in path:
            return "/projects/resource-ledger", "manage_capabilities"
        if "/maintenance" in path:
            return "/projects/resource-ledger", "manage_maintenance"
        action = "create" if method == "POST" else "delete" if method == "DELETE" else "edit"
        return "/projects/resource-ledger", action
    if path.startswith("/api/v1/projects"):
        if "/plan-drafts/commit" in path:
            return "/projects/plan-breakdown", "save_draft"
        if "/tasks" in path:
            action = "edit_task" if "/reorder" in path else "create_task" if method == "POST" else "delete_task" if method == "DELETE" else "edit_task"
            return "/projects/plan-breakdown", action
        if "import-standard-plan" in path:
            return "/projects/plan-breakdown", "import_template"
        if "approval-gates" in path:
            return "/projects/plan-breakdown", "approval_gate"
        if "/plan-" in path:
            return "/projects/plan-breakdown", "schedule"
        action = "create" if method == "POST" else "delete" if method == "DELETE" else "edit"
        return "/projects/ledger", action
    if path.startswith("/api/v1/schedules/insert-order"):
        return "/schedule/insert-order", "confirm" if "confirm" in path else "preview"
    if path.startswith("/api/v1/schedules/apply-project-plan"):
        return "/projects/plan-breakdown", "schedule"
    if path.startswith("/api/v1/schedules/daily-roll"):
        return "/schedule/engine", "daily_roll"
    if path.startswith("/api/v1/schedules/timeslots"):
        action = next((key for key in ("complete", "pause", "interrupt", "delay", "night-run", "start") if key in path), "manual_edit")
        return ("/tasks/workspace", action.replace("night-run", "night_run")) if action != "manual_edit" else ("/schedule/engine", action)
    if path.startswith("/api/v1/schedules"):
        return "/schedule/engine", "reschedule" if "reschedule" in path else "generate"
    return None


def require_management_user(user: User = Depends(require_authenticated_user)) -> User:
    return user


def require_project_editor_by_project_id(
    project_id: int,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> User:
    _ensure_access(lambda: ensure_project_editor(db, project_id, user))
    return user


def require_project_editor_by_proj_id(
    proj_id: int,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> User:
    _ensure_access(lambda: ensure_project_editor(db, proj_id, user))
    return user


def require_task_editor(
    task_id: int,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> User:
    _ensure_access(lambda: ensure_task_editor(db, task_id, user))
    return user


def require_slot_operator(
    slot_id: int,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> User:
    _ensure_access(lambda: ensure_slot_operator(db, slot_id, user))
    return user


def require_notification_owner(
    nid: int,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> User:
    _ensure_access(lambda: ensure_notification_owner(db, nid, user))
    return user


def _ensure_access(callback: Callable[[], object]) -> None:
    try:
        callback()
    except AccessResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
