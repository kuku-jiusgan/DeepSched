from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.users import require_authenticated_user
from app.core.database import get_db
from app.models import Instrument, Project, Task, TaskDependency, TaskTypeConfig, TimeSlot, User
from app.services.audit_log_service import list_audit_logs, project_audit_detail
from app.services.role_permission_service import permissions_for_roles
from app.services.user_role_service import user_roles


router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit-logs"])


@router.get("")
def get_audit_logs(
    keyword: str | None = None,
    action: str | None = None,
    user_name: str | None = None,
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    permission = next(
        item for item in permissions_for_roles(db, user_roles(user))
        if item["page_key"] == "/system/audit-logs"
    )
    if not permission["can_view"]:
        raise HTTPException(status_code=403, detail="当前角色没有查看操作日志的权限")
    return [
        {
            "id": item.id,
            "user_name": _operator_display_name(db, item.user_name),
            "action": item.action,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "detail": _enriched_detail(db, item),
            "created_at": item.created_at,
        }
        for item in list_audit_logs(db, keyword, action, user_name, start_at, end_at)
    ]


def _operator_display_name(db: Session, operator: str) -> str:
    if operator in {"system", "anonymous"}:
        return operator
    user = db.query(User).filter(
        (User.username == operator) | (User.display_name == operator)
    ).first()
    return user.display_name if user else operator


def _enriched_detail(db: Session, item) -> dict:
    detail = dict(item.detail or {})
    if item.action == "project_plan_drafts_committed" and not detail.get("task_details"):
        detail = _legacy_project_plan_detail(db, item.target_id, detail)
    if item.action == "schedule_insert_confirmed" and not detail.get("insert_summary"):
        detail = _legacy_insert_detail(db, detail)
    if item.action == "HTTP POST" and detail.get("path") == "/api/v1/projects":
        project = db.query(Project).filter(
            Project.created_at >= item.created_at - timedelta(seconds=1),
            Project.created_at <= item.created_at + timedelta(seconds=1),
        ).first()
        if project:
            detail.update(project_audit_detail(project))
    task_id = detail.get("task_id")
    slot = None
    if not task_id and item.target_type == "time_slot" and item.target_id:
        slot = db.query(TimeSlot).filter(TimeSlot.id == item.target_id).first()
        task_id = slot.task_id if slot else None
    if item.target_type == "time_slot" and item.target_id:
        slot = slot or db.query(TimeSlot).filter(TimeSlot.id == item.target_id).first()
        if slot:
            instrument_name = slot.instrument.name if slot.instrument else "未指定仪器"
            detail["target_display"] = (
                f"{slot.plan_start:%Y-%m-%d} · {instrument_name} · "
                f"{slot.plan_start:%H:%M}–{slot.plan_end:%H:%M}"
            )
    if task_id and not detail.get("task_display"):
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            project = task.project
            detail["task_display"] = " · ".join(part for part in [
                project.code if project else None,
                project.name if project else None,
                task.name,
            ] if part)
    return detail


def _legacy_insert_detail(db: Session, detail: dict) -> dict:
    task_ids = detail.get("task_ids") or []
    tasks = db.query(Task).filter(Task.id.in_(task_ids)).all() if task_ids else []
    task_by_id = {task.id: task for task in tasks}
    inserted = "、".join(
        _audit_task_display(task_by_id[task_id])
        for task_id in task_ids
        if task_id in task_by_id
    ) or "未知任务"
    anchor = db.query(Task).filter(Task.id == detail.get("anchor_task_id")).first()
    return {
        "insert_summary": (
            f"将【{inserted}】插入到"
            f"【{_audit_task_display(anchor) if anchor else '未知任务'}】之后"
        ),
        "moved_tasks": detail.get("moved_tasks", 0),
        "schedule_run_id": detail.get("schedule_run_id", ""),
    }


def _legacy_project_plan_detail(db: Session, project_id: int | None, detail: dict) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return detail
    created = int(detail.get("created") or 0)
    tasks = db.query(Task).filter(Task.project_id == project.id).order_by(
        Task.id
    ).limit(created).all()
    task_ids = {task.id for task in tasks}
    project_task_ids = {
        task_id for task_id, in db.query(Task.id).filter(
            Task.project_id == project.id
        ).all()
    }
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(task_ids),
        TaskDependency.predecessor_id.in_(project_task_ids),
    ).all() if task_ids else []
    predecessor_ids = {item.predecessor_id for item in dependencies}
    related_tasks = db.query(Task).filter(
        Task.id.in_(task_ids | predecessor_ids)
    ).all() if task_ids or predecessor_ids else []
    task_names = {task.id: task.name for task in related_tasks}
    predecessors_by_task: dict[int, list[str]] = {}
    for dependency in dependencies:
        predecessors_by_task.setdefault(dependency.task_id, []).append(
            task_names.get(dependency.predecessor_id, f"任务 #{dependency.predecessor_id}")
        )
    type_names = {item.code: item.name for item in db.query(TaskTypeConfig).all()}
    instruments = {
        item.id: " · ".join(part for part in [item.code, item.name] if part)
        for item in db.query(Instrument).all()
    }
    return {
        "target_display": f"{project.code} · {project.name}",
        "created": created,
        "task_details": [
            {
                "name": task.name,
                "task_type": (
                    "方案签批" if task.is_external_gate
                    else "任务组" if task.task_type == "group"
                    else type_names.get(task.task_type, task.task_type)
                ),
                "estimated_hours": task.est_duration_hours,
                "assignee": task.assignee.display_name if task.assignee else "未指定负责人",
                "instruments": [instruments.get(item, f"仪器 #{item}") for item in (task.instrument_ids or [])],
                "predecessors": predecessors_by_task.get(task.id, []),
                "parent": task.parent.name if task.parent else None,
            }
            for task in tasks
        ],
    }


def _audit_task_display(task: Task) -> str:
    project = task.project
    project_display = " · ".join(
        part for part in [project.code, project.name] if part
    ) if project else "未知项目"
    return f"{project_display} · {task.name}"
