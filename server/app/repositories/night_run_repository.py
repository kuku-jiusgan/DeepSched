from sqlalchemy.orm import joinedload

from app.models import TaskNightRun, TimeSlot


def list_pending_night_runs(db):
    return (
        db.query(TaskNightRun)
        .join(TimeSlot, TimeSlot.id == TaskNightRun.slot_id)
        .options(joinedload(TaskNightRun.slot))
        .filter(
            TaskNightRun.lifecycle_status == "active",
            TimeSlot.lifecycle_status == "active",
            TimeSlot.is_night_run.is_(True),
            TimeSlot.status.in_(["scheduled", "running"]),
        )
        .order_by(TaskNightRun.started_at, TaskNightRun.id)
        .with_for_update()
        .all()
    )
