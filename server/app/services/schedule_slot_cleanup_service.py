from sqlalchemy.orm import Session

from app.models import Task, TimeSlot
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.task_delay_status_service import NOT_DELAYED_STATUS


def clear_reschedulable_slots(db: Session, project_ids: list[int] | None = None) -> None:
    completed_task_ids = db.query(Task.id).filter(Task.status == "completed").distinct()

    if project_ids:
        project_task_ids = db.query(Task.id).filter(Task.project_id.in_(project_ids))
        delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
            TimeSlot.task_id.in_(project_task_ids),
            TimeSlot.status.in_(["scheduled", "blocked"]),
            ~TimeSlot.task_id.in_(completed_task_ids),
        ), synchronize_session=False)

        db.query(Task).filter(
            Task.project_id.in_(project_ids),
            Task.status.in_(["scheduled", "blocked"]),
            ~Task.id.in_(completed_task_ids),
        ).update(
            {"status": "pending", "delay_status": NOT_DELAYED_STATUS},
            synchronize_session=False,
        )
        db.commit()
        return

    frozen_task_ids = db.query(TimeSlot.task_id).filter(
        TimeSlot.tier == "frozen"
    ).distinct()

    delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
        TimeSlot.tier.in_(["forecast", "confirmed"]),
        TimeSlot.status.in_(["scheduled", "blocked"]),
        ~TimeSlot.task_id.in_(frozen_task_ids),
        ~TimeSlot.task_id.in_(completed_task_ids),
    ), synchronize_session=False)

    db.query(Task).filter(
        Task.status.in_(["scheduled", "blocked"]),
        ~Task.id.in_(frozen_task_ids),
    ).update(
        {"status": "pending", "delay_status": NOT_DELAYED_STATUS},
        synchronize_session=False,
    )

    db.commit()
