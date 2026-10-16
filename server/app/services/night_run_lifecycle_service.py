import logging
from datetime import datetime

from app.domain.errors import DomainConflictError
from app.domain.night_run import night_run_status
from app.repositories.night_run_repository import list_pending_night_runs


_logger = logging.getLogger(__name__)


def advance_night_runs(db, now: datetime | None = None) -> int:
    current_time = now or datetime.now()
    changed = 0
    for record in list_pending_night_runs(db):
        slot = record.slot
        if (slot.task_id != record.task_id or slot.instrument_id != record.instrument_id
                or slot.plan_start != record.started_at or slot.plan_end != record.ended_at):
            raise DomainConflictError(f"夜间运行登记与时间槽不一致：时间槽 {slot.id}")
        status = night_run_status(record.started_at, record.ended_at, current_time)
        if status == slot.status:
            continue
        _logger.info("night_run_advanced slot_id=%s previous=%s status=%s", slot.id, slot.status, status)
        slot.status = status
        changed += 1
    db.flush()
    return changed
