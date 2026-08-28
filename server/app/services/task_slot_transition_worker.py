from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime

from app.core.database import SessionLocal
from app.models import Task
from app.repositories.worker_lease_repository import acquire_worker_lease

REFRESH_INTERVAL_SECONDS = 60
LEASE_NAME = "task-slot-transition"
LEASE_SECONDS = 90
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def start_task_slot_transition_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_run_loop, name="task-slot-transition-worker", daemon=True)
    _worker_thread.start()


def stop_task_slot_transition_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _run_loop() -> None:
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            if acquire_worker_lease(db, LEASE_NAME, _owner_id, LEASE_SECONDS):
                advance_running_tasks(db, datetime.now())
        except Exception:
            db.rollback()
            _logger.exception("任务时间槽状态推进失败")
        finally:
            db.close()
        _stop_event.wait(REFRESH_INTERVAL_SECONDS)


def advance_running_tasks(db, now: datetime) -> int:
    changed = 0
    tasks = db.query(Task).filter(Task.status == "running").all()
    for task in tasks:
        slots = sorted(
            (slot for slot in task.time_slots if slot.lifecycle_status == "active" and slot.plan_end and slot.plan_end > slot.plan_start),
            key=lambda slot: (slot.plan_start, slot.id),
        )
        if not slots:
            continue
        if not any(slot.actual_start is not None for slot in task.time_slots):
            continue
        for slot in slots[:-1]:
            if slot.plan_end <= now and slot.status in {"scheduled", "running"}:
                slot.status = "completed"
                slot.actual_start = slot.actual_start or slot.plan_start
                # 计划结束早于实际开始时（计划时间已过才点开始），直接填计划
                # 结束会写出"结束早于开始"的矛盾数据，流进工时统计。
                slot.actual_end = slot.actual_end or max(slot.plan_end, slot.actual_start)
                changed += 1
        for slot in slots:
            if slot.status == "running" and slot.actual_start is None:
                slot.actual_start = slot.plan_start
                changed += 1
        current = next((slot for slot in slots if slot.plan_start <= now < slot.plan_end), None)
        if current is None:
            final_slot = slots[-1]
            if final_slot.plan_start <= now and final_slot.status == "scheduled":
                final_slot.status = "running"
                final_slot.actual_start = final_slot.actual_start or final_slot.plan_start
                changed += 1
            continue
        if current.status == "scheduled":
            current.status = "running"
            current.actual_start = current.actual_start or current.plan_start
            changed += 1
    if changed:
        db.commit()
    return changed
