from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload, selectinload

from app.models import AuditLog, Task, TimeSlot, User
from app.services.user_role_service import has_any_role


WORKSPACE_ALL_TASK_ROLES = {"系统管理员", "项目管理员"}
WORKSPACE_TASK_STATUSES = {"pending", "running", "paused", "blocked", "scheduled", "completed", "done", "interrupted"}
WORKSPACE_SLOT_STATUSES = {"scheduled", "running", "paused", "interrupted", "blocked", "completed"}
AGENDA_OVERDUE_SLOT_STATUSES = WORKSPACE_SLOT_STATUSES - {"completed"}
AGENDA_CURRENT_ACTIVITY_SLOT_STATUSES = {"running", "paused", "interrupted"}
AGENDA_ACTIVITY_END_SLOT_STATUSES = {"paused", "interrupted"}
COMPLETED_TASK_STATUSES = {"done", "completed"}


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
        (
            slot for slot in task.time_slots
            if slot.lifecycle_status == "active"
            and slot.status in WORKSPACE_SLOT_STATUSES
            and slot.plan_start
            and slot.plan_end
            and slot.plan_end > slot.plan_start
        ),
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


def list_recent_pause_switch_logs(db) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(AuditLog.action == "task_paused")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(100)
        .all()
    )


def list_tasks_by_ids(db, task_ids: set[int]) -> list[Task]:
    if not task_ids:
        return []
    return (
        db.query(Task)
        .filter(Task.id.in_(task_ids))
        .options(joinedload(Task.project))
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


def get_active_user(db, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()


def list_agenda_slots(db, assignee_id: int, start_at, end_at, today_start=None, today_end=None) -> list[TimeSlot]:
    time_filters = [
        and_(
            TimeSlot.plan_start < end_at,
            TimeSlot.plan_end > start_at,
        )
    ]
    if today_start is not None and today_end is not None:
        unfinished_slot = and_(
            TimeSlot.status.in_(AGENDA_OVERDUE_SLOT_STATUSES),
            Task.status.notin_(COMPLETED_TASK_STATUSES),
        )
        time_filters.append(
            and_(
                or_(
                    and_(
                        unfinished_slot,
                        TimeSlot.plan_end <= today_start,
                        or_(
                            Task.delay_status == "delayed",
                            TimeSlot.actual_start.is_not(None),
                        ),
                    ),
                    and_(
                        unfinished_slot,
                        TimeSlot.status.in_(AGENDA_CURRENT_ACTIVITY_SLOT_STATUSES),
                        TimeSlot.actual_start.is_not(None),
                        TimeSlot.actual_start < today_end,
                        or_(
                            TimeSlot.actual_end.is_(None),
                            TimeSlot.actual_end >= today_start,
                        ),
                    ),
                    and_(
                        unfinished_slot,
                        TimeSlot.status.in_(AGENDA_ACTIVITY_END_SLOT_STATUSES),
                        TimeSlot.actual_end >= today_start,
                        TimeSlot.actual_end < today_end,
                    ),
                ),
            )
        )
    return (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            Task.assignee_id == assignee_id,
            Task.is_external_gate.is_(False),
            ~Task.children.any(),
            TimeSlot.status.in_(WORKSPACE_SLOT_STATUSES),
            TimeSlot.lifecycle_status == "active",
            or_(*time_filters),
        )
        .options(
            joinedload(TimeSlot.task).joinedload(Task.project),
            joinedload(TimeSlot.task).selectinload(Task.time_slots),
            joinedload(TimeSlot.task).selectinload(Task.execution_segments),
            joinedload(TimeSlot.instrument),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
