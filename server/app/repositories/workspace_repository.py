from __future__ import annotations

from sqlalchemy.orm import joinedload, selectinload

from app.models import AuditLog, Task, TimeSlot
from app.services.user_role_service import has_any_role


WORKSPACE_ALL_TASK_ROLES = {"系统管理员"}
WORKSPACE_TASK_STATUSES = {"pending", "running", "paused", "blocked", "scheduled", "done", "interrupted"}
WORKSPACE_SLOT_STATUSES = {"scheduled", "running", "paused", "interrupted", "blocked", "completed"}


def list_workspace_tasks(db, user) -> list[Task]:
    query = (
        db.query(Task)
        .filter(
            Task.status.in_(WORKSPACE_TASK_STATUSES),
            Task.is_external_gate.is_(False),
            ~Task.children.any(),
        )
        .options(
            joinedload(Task.project),
            joinedload(Task.assignee),
            selectinload(Task.time_slots).joinedload(TimeSlot.instrument),
            selectinload(Task.execution_segments),
        )
    )
    query = filter_workspace_tasks_by_user(query, user)
    return query.order_by(Task.id).all()


def filter_workspace_tasks_by_user(query, user):
    if has_any_role(user, WORKSPACE_ALL_TASK_ROLES):
        return query
    return query.filter(Task.assignee_id == user.id)


def workspace_segments(task: Task) -> list[TimeSlot]:
    return sorted(
        (slot for slot in task.time_slots if slot.status in WORKSPACE_SLOT_STATUSES),
        key=lambda slot: (slot.plan_start, slot.id),
    )


def list_delay_logs(db, slot_ids: list[int]) -> list[AuditLog]:
    if not slot_ids:
        return []
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "task_delay_reported",
            AuditLog.target_id.in_(slot_ids),
        )
        .order_by(AuditLog.created_at.desc())
        .all()
    )


def latest_open_task_slot(task_id: int, db) -> TimeSlot | None:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == task_id,
            TimeSlot.status.in_(["scheduled", "running"]),
        )
        .order_by(TimeSlot.plan_end.desc(), TimeSlot.id.desc())
        .first()
    )


def get_time_slot(db, slot_id: int) -> TimeSlot | None:
    return db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()


def get_task(db, task_id: int) -> Task | None:
    return db.query(Task).filter(Task.id == task_id).first()
