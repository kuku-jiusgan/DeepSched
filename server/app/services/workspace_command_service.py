from datetime import datetime
import logging

from app.domain.errors import DomainNotFoundError, DomainValidationError
from app.repositories.workspace_repository import get_task, get_time_slot
from app.services.schedule_completion_service import complete_task_and_shift
from app.services.instrument_status_service import refresh_instrument_status
from app.services.task_delay_status_service import mark_task_delayed
from app.services.task_progress_service import planned_task_minutes


_logger = logging.getLogger(__name__)


def complete_workspace_task(db, slot_id: int, release_instrument: bool) -> dict:
    slot = get_time_slot(db, slot_id)
    if slot is None:
        _logger.warning("complete_task_rejected slot_id=%s reason=slot_not_found", slot_id)
        raise DomainNotFoundError("时间槽不存在")
    task = get_task(db, slot.task_id)
    if task is None:
        _logger.warning("complete_task_rejected slot_id=%s reason=task_not_found", slot_id)
        raise DomainNotFoundError("任务不存在")
    progress_complete = int(task.executed_minutes or 0) >= planned_task_minutes(task)
    _logger.info(
        "complete_task_requested slot_id=%s task_id=%s task_status=%s executed_minutes=%s planned_minutes=%s release_instrument=%s",
        slot_id, task.id, task.status, task.executed_minutes, planned_task_minutes(task), release_instrument,
    )
    if (task.status != "running" and not (task.status == "paused" and progress_complete)) or not any(
        task_slot.actual_start is not None
        for task_slot in task.time_slots
    ):
        _logger.warning(
            "complete_task_rejected slot_id=%s task_id=%s reason=not_started_or_invalid_state",
            slot_id, task.id,
        )
        raise DomainValidationError("任务尚未开始，不能直接完成")
    try:
        result = complete_task_and_shift(
            db,
            slot.task_id,
            completed_slot_id=slot.id,
            release_instrument=release_instrument,
        )
    except Exception:
        _logger.exception(
            "complete_task_failed slot_id=%s task_id=%s release_instrument=%s",
            slot_id, task.id, release_instrument,
        )
        raise
    if result.get("status") == "error":
        _logger.error("complete_task_replan_failed slot_id=%s task_id=%s result=%s", slot_id, task.id, result)
        raise DomainValidationError(result.get("message") or "任务完成失败")
    _logger.info("complete_task_succeeded slot_id=%s task_id=%s result=%s", slot_id, task.id, result)
    return result


def interrupt_workspace_task(db, slot_id: int) -> dict:
    slot = get_time_slot(db, slot_id)
    if slot is None:
        raise DomainNotFoundError("时间槽不存在")
    task = get_task(db, slot.task_id)
    if task is None:
        raise DomainNotFoundError("任务不存在")
    slot.status = "interrupted"
    slot.actual_end = datetime.now()
    task.status = "blocked"
    mark_task_delayed(task)
    db.flush()
    refresh_instrument_status(db, slot.instrument_id)
    return {"status": "ok"}
