from datetime import datetime
import logging

from app.domain.errors import DomainNotFoundError, DomainValidationError
from app.models import Task
from app.repositories.workspace_repository import get_task, get_time_slot
from app.services.schedule_completion_service import complete_task_and_shift
from app.services.instrument_status_service import refresh_instrument_status
from app.services.task_delay_status_service import mark_task_delayed
from app.services.task_progress_service import planned_task_minutes


_logger = logging.getLogger(__name__)

COMPLETED_TASK_STATUSES = {"completed", "done"}


def complete_workspace_task(db, slot_id: int, release_instrument: bool) -> dict:
    slot = get_time_slot(db, slot_id)
    if slot is None:
        _logger.warning("complete_task_rejected slot_id=%s reason=slot_not_found", slot_id)
        raise DomainNotFoundError("时间槽不存在")
    # 先对任务行加锁再判状态。完成任务会触发资源释放重排，实测耗时二三十秒，
    # 期间用户看不到任何反馈就会反复点击。没有锁的话，多个请求各自读到同一份
    # 「未完成」的旧快照、各自跑一遍重排、各自落一份时间槽——删了一次、插了
    # 五次，同一任务留下五份完全重叠的副本。加锁后并发请求排队，后到的能看到
    # 前一个已经把任务置为完成，直接走下面的幂等分支返回。
    task = _lock_task(db, slot.task_id)
    if task is None:
        _logger.warning("complete_task_rejected slot_id=%s reason=task_not_found", slot_id)
        raise DomainNotFoundError("任务不存在")
    if task.status in COMPLETED_TASK_STATUSES:
        # 幂等：重复提交不再报错、更不再触发第二次重排。
        _logger.info(
            "complete_task_ignored_duplicate slot_id=%s task_id=%s", slot_id, task.id,
        )
        return {
            "status": "ok",
            "message": "任务已完成",
            "moved_tasks": 0,
            "released_instrument": False,
            "duplicate": True,
        }
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


def _lock_task(db, task_id: int):
    """取任务并加行锁，让并发的完成请求排队而不是各跑各的。

    SQLite 不支持 SELECT ... FOR UPDATE，测试库上退化为普通查询；正式库是
    MySQL，锁真实生效。
    """
    query = db.query(Task).filter(Task.id == task_id)
    try:
        return query.with_for_update().first()
    except Exception:  # 不支持行锁的方言
        return query.first()


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
