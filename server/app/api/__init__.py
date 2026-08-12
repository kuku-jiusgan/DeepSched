from fastapi import APIRouter, Depends

from app.api import (
    alert_rules,
    audit_logs,
    approval_gates,
    calendar_api,
    detection_tasks,
    instruments,
    notifications,
    project_plan_drafts,
    project_plan_schedules,
    project_plan_templates,
    project_hours_reports,
    role_permissions,
    projects,
    schedule_rules,
    schedules,
    stats,
    task_types,
)
from app.api.users import require_authenticated_user
from app.api.access import require_configured_operation


protected_router = APIRouter(dependencies=[
    Depends(require_authenticated_user),
    Depends(require_configured_operation),
])

for router in (
    instruments.router,
    detection_tasks.router,
    role_permissions.router,
    projects.router,
    project_hours_reports.router,
    schedules.router,
    project_plan_schedules.router,
    schedule_rules.router,
    stats.router,
    notifications.router,
    task_types.router,
    alert_rules.router,
    audit_logs.router,
    calendar_api.router,
    approval_gates.router,
    project_plan_templates.router,
    project_plan_drafts.router,
):
    protected_router.include_router(router)
