from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Task, TimeSlot
from app.schemas.schemas import RescheduleRequest
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.schedule_advance_notification_service import (
    ScheduleWindow,
    capture_task_schedule_windows,
)
from app.services.task_delay_status_service import NOT_DELAYED_STATUS, reset_task_delay


def reschedule(db: Session, data: RescheduleRequest) -> dict:
    if data.strategy == "local":
        return _local_repair(db, data)
    if data.strategy == "project":
        return _project_reschedule(db, data)
    return _global_reschedule(db)


def _local_repair(db: Session, data: RescheduleRequest) -> dict:
    original_windows = {}
    if data.affected_task_id:
        task = db.query(Task).filter(Task.id == data.affected_task_id).first()
        if task and task.status not in {"running", "done", "completed"}:
            original_windows = capture_task_schedule_windows(db, {task.id})
            delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
                TimeSlot.task_id == data.affected_task_id,
                TimeSlot.tier.in_(["confirmed", "forecast"]),
                TimeSlot.status.in_(["scheduled", "blocked", "paused", "interrupted"]),
            ))
            task.status = "pending"
            reset_task_delay(task)
            db.commit()
    return _generate(
        db,
        original_windows=original_windows,
        advance_reason="局部重排",
    )


def _project_reschedule(db: Session, data: RescheduleRequest) -> dict:
    if data.affected_task_id:
        task = db.query(Task).filter(Task.id == data.affected_task_id).first()
        if task and task.status not in {"running", "done", "completed"}:
            project_task_ids = {
                task_id for task_id, in db.query(Task.id).filter(
                    Task.project_id == task.project_id,
                ).all()
            }
            original_windows = capture_task_schedule_windows(db, project_task_ids)
            delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
                TimeSlot.task_id.in_(
                    db.query(Task.id).filter(Task.project_id == task.project_id)
                ),
                or_(
                    (
                        TimeSlot.tier.in_(["confirmed", "forecast"])
                        & TimeSlot.status.in_(["scheduled", "blocked"])
                    ),
                    TimeSlot.status.in_(["paused", "interrupted"]),
                ),
            ))
            db.query(Task).filter(
                Task.project_id == task.project_id,
                Task.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
            ).update({"status": "pending", "delay_status": NOT_DELAYED_STATUS})
            db.commit()
            return _generate(
                db,
                [task.project_id],
                original_windows=original_windows,
                advance_reason="项目重排",
                current_project_id=task.project_id,
            )
    return {"status": "error", "message": "未指定受影响任务"}


def _global_reschedule(db: Session) -> dict:
    locked_task_ids = _locked_task_ids(db)
    movable_slots = db.query(TimeSlot).filter(
        TimeSlot.tier.in_(["confirmed", "forecast"]),
        TimeSlot.status.in_(["scheduled", "blocked"]),
    )
    if locked_task_ids:
        movable_slots = movable_slots.filter(
            ~TimeSlot.task_id.in_(locked_task_ids),
        )
    movable_task_rows = movable_slots.with_entities(
        TimeSlot.task_id,
    ).distinct().all()
    movable_task_ids = {task_id for task_id, in movable_task_rows}
    original_windows = capture_task_schedule_windows(db, movable_task_ids)
    delete_time_slots_and_refresh(
        db,
        movable_slots,
        synchronize_session=False,
    )
    if movable_task_ids:
        db.query(Task).filter(
            Task.id.in_(movable_task_ids),
            Task.status == "scheduled",
        ).update(
            {"status": "pending", "delay_status": NOT_DELAYED_STATUS},
            synchronize_session=False,
        )
    result = _generate(
        db,
        commit=False,
        excluded_task_ids=locked_task_ids,
        original_windows=original_windows,
        advance_reason="全局重排",
    )
    if result.get("status") == "ok":
        db.commit()
    else:
        db.rollback()
    return result


def _generate(
    db: Session,
    project_ids: list[int] | None = None,
    commit: bool = True,
    excluded_task_ids: set[int] | None = None,
    original_windows: dict[int, ScheduleWindow] | None = None,
    advance_reason: str = "重新排程",
    current_project_id: int | None = None,
) -> dict:
    from app.services.scheduler import SchedulerService

    return SchedulerService(db).generate(
        project_ids,
        commit=commit,
        excluded_task_ids=excluded_task_ids,
        original_schedule_windows=original_windows,
        advance_notification_reason=advance_reason,
        current_project_id=current_project_id,
    )


def _locked_task_ids(db: Session) -> set[int]:
    rows = db.query(TimeSlot.task_id).filter(
        (TimeSlot.tier == "frozen")
        | TimeSlot.status.in_(["running", "completed"]),
    ).distinct().all()
    return {task_id for task_id, in rows}
