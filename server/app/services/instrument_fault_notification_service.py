from __future__ import annotations

from datetime import datetime

from app.models import Instrument, Task
from app.services.push_notification_service import push_by_rule


def notify_fault_rescheduled_assignees(
    db,
    instrument: Instrument,
    tasks,
    estimated_resolved_at: datetime,
    shifted_slots: int,
) -> int:
    notified = 0
    task_names = "、".join(task.name for task in tasks if task is not None)
    notify_users = []
    for task in tasks:
        if not task or not task.assignee or not task.assignee.username:
            continue
        notify_users.append(task.assignee)
    if notify_users:
        notified = push_by_rule(
            db,
            "instrument_fault_reschedule",
            notify_users,
            f"{instrument.name} 故障影响排程",
            (
                f"仪器 {instrument.name}({instrument.code}) 已提报故障，"
                f"预计维修完成时间为 {estimated_resolved_at:%Y-%m-%d %H:%M}。"
                f"受影响任务：{task_names or '暂无'}，已后移 {shifted_slots} 个时间槽。"
            ),
            related_entity_type="instrument",
            related_entity_id=instrument.id,
        )
    return notified


def notify_fault_schedule_risks(
    db,
    instrument: Instrument,
    violations: list[dict],
    estimated_resolved_at: datetime,
) -> int:
    notified = 0
    notify_items = []
    for violation in violations:
        task = db.query(Task).filter(Task.id == violation["task_id"]).first()
        manager = task.project.manager if task and task.project else None
        if not manager or not manager.username:
            continue
        notify_items.append((manager, violation))
    for manager, violation in notify_items:
        notified += push_by_rule(
            db,
            "instrument_fault_schedule_conflict",
            [manager],
            f"{instrument.name} 故障导致排程超期风险",
            (
                f"仪器 {instrument.name}({instrument.code}) 预计维修完成时间为 "
                f"{estimated_resolved_at:%Y-%m-%d %H:%M}。{violation['reason']}"
            ),
            related_entity_type="task",
            related_entity_id=violation["task_id"],
            context_roles=["项目负责人"],
        )
    return notified
