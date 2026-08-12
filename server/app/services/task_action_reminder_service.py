from __future__ import annotations

import threading
import logging
import uuid
from datetime import datetime, timedelta

from app.core.database import SessionLocal
from app.models import AlertRule, Notification, Task
from app.services.push_notification_service import push_by_rule
from app.services.task_delay_status_service import mark_task_delayed
from app.services.task_execution_service import COMPLETED_TASK_STATUSES
from app.repositories.worker_lease_repository import acquire_worker_lease


REMINDER_SCAN_INTERVAL_SECONDS = 30
REMINDER_LEASE_SECONDS = 45
REMINDER_LEASE_NAME = "task-action-reminders"
START_REMINDER_TYPE = "task_start_delay"
END_REMINDER_TYPE = "task_end_delay"
ACTIVE_SLOT_STATUSES = {"scheduled", "running", "paused", "blocked", "interrupted", "completed"}

_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def start_task_action_reminder_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_reminder_loop,
        name="task-action-reminder-worker",
        daemon=True,
    )
    _worker_thread.start()


def stop_task_action_reminder_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def scan_task_action_reminders(db, now: datetime | None = None) -> dict[str, int]:
    current_time = now or datetime.now()
    start_rule = _rule(db, START_REMINDER_TYPE)
    end_rule = _rule(db, END_REMINDER_TYPE)
    start_threshold_minutes = max(0, start_rule.threshold_minutes if start_rule else 15)
    start_threshold = timedelta(minutes=start_threshold_minutes)
    start_count = 0
    end_count = 0

    tasks = db.query(Task).filter(Task.is_external_gate.is_(False)).all()
    for task in tasks:
        if task.children or task.status in COMPLETED_TASK_STATUSES:
            continue
        slots = [slot for slot in task.time_slots if slot.status in ACTIVE_SLOT_STATUSES]
        if not slots:
            continue
        first_slot = min(slots, key=lambda slot: (slot.plan_start, slot.id))
        last_slot = max(slots, key=lambda slot: (slot.plan_end, slot.id))
        has_started = task.status == "running" or any(slot.actual_start for slot in slots)

        reminder_at = first_slot.plan_start - start_threshold
        if not has_started and reminder_at <= current_time < first_slot.plan_start:
            if not _already_notified(db, START_REMINDER_TYPE, first_slot.id):
                start_count += _push_start_reminder(
                    db, task, first_slot, start_threshold_minutes,
                )

        if not has_started and current_time >= first_slot.plan_start:
            mark_task_delayed(task)

        if has_started and current_time >= last_slot.plan_end:
            mark_task_delayed(task)
            if not _already_notified(db, END_REMINDER_TYPE, last_slot.id):
                end_count += _push_end_reminder(db, task, last_slot)

    db.commit()
    return {"start_reminders": start_count, "end_reminders": end_count}


def _push_start_reminder(db, task: Task, slot, threshold_minutes: int) -> int:
    return push_by_rule(
        db,
        START_REMINDER_TYPE,
        [task.assignee],
        f"任务将在 {threshold_minutes} 分钟后开始",
        (
            f"项目【{task.project.code if task.project else '-'}】任务【{task.name}】"
            f"计划于 {slot.plan_start:%Y-%m-%d %H:%M} 开始，请提前做好准备。"
        ),
        related_entity_type="time_slot",
        related_entity_id=slot.id,
        context_roles=["任务负责人"],
    )


def _push_end_reminder(db, task: Task, slot) -> int:
    return push_by_rule(
        db,
        END_REMINDER_TYPE,
        _recipients(task),
        "任务已到计划结束时间，请点击结束",
        (
            f"项目【{task.project.code if task.project else '-'}】任务【{task.name}】"
            f"计划于 {slot.plan_end:%Y-%m-%d %H:%M} 结束，当前尚未点击结束，"
            "请及时完成任务或上报延期。"
        ),
        related_entity_type="time_slot",
        related_entity_id=slot.id,
        context_roles=["任务负责人", "项目负责人"],
    )


def _recipients(task: Task) -> list:
    return [task.assignee, task.project.manager if task.project else None]


def _already_notified(db, reminder_type: str, slot_id: int) -> bool:
    return db.query(Notification.id).filter(
        Notification.n_type == reminder_type,
        Notification.channel == "site",
        Notification.related_entity_type == "time_slot",
        Notification.related_entity_id == slot_id,
    ).first() is not None


def _rule(db, rule_type: str) -> AlertRule | None:
    return db.query(AlertRule).filter(AlertRule.rule_type == rule_type).first()


def _reminder_loop() -> None:
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            if acquire_worker_lease(
                db,
                REMINDER_LEASE_NAME,
                _worker_owner_id,
                REMINDER_LEASE_SECONDS,
            ):
                scan_task_action_reminders(db)
        except Exception:
            db.rollback()
            _logger.exception("任务提醒后台扫描失败")
        finally:
            db.close()
        _stop_event.wait(REMINDER_SCAN_INTERVAL_SECONDS)
