from __future__ import annotations

from datetime import datetime

from app.models import Task, TimeSlot
from app.services.push_notification_service import push_by_rule


ADVANCE_NOTIFICATION_RULE_TYPE = "task_schedule_advanced"
DELAYED_NOTIFICATION_RULE_TYPE = "task_schedule_delayed"
ScheduleWindow = tuple[datetime, datetime]


def _format_time_change(total_seconds: float) -> str:
    hours = max(0.1, round(abs(total_seconds) / 3600, 1))
    if hours > 24:
        days = round(hours / 24, 1)
        return f"{days:g} 天"
    return f"{hours:g} 小时"


def capture_task_schedule_windows(
    db,
    task_ids: set[int] | list[int],
) -> dict[int, ScheduleWindow]:
    ids = set(task_ids)
    if not ids:
        return {}

    slots = (
        db.query(TimeSlot)
        .filter(TimeSlot.task_id.in_(ids))
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    windows: dict[int, ScheduleWindow] = {}
    for slot in slots:
        current = windows.get(slot.task_id)
        if current is None:
            windows[slot.task_id] = (slot.plan_start, slot.plan_end)
            continue
        windows[slot.task_id] = (
            min(current[0], slot.plan_start),
            max(current[1], slot.plan_end),
        )
    return windows


def notify_rescheduled_tasks_advanced(
    db,
    original_windows: dict[int, ScheduleWindow] | None,
    reason: str = "重新排程",
) -> int:
    if not original_windows:
        return 0

    new_windows = capture_task_schedule_windows(db, set(original_windows))
    sent = 0
    for task_id, original_window in original_windows.items():
        new_window = new_windows.get(task_id)
        if not new_window or new_window[0] >= original_window[0]:
            continue
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task or not task.assignee:
            continue
        project_code = task.project.code if task.project else ""
        task_label = f"{project_code} · {task.name}" if project_code else task.name
        advanced_duration = _format_time_change(
            (original_window[0] - new_window[0]).total_seconds(),
        )
        sent += push_by_rule(
            db,
            ADVANCE_NOTIFICATION_RULE_TYPE,
            [task.assignee],
            "排程调整后，您的任务已前移",
            (
                f"因{reason}，您的任务“{task_label}”计划开始时间由 "
                f"{original_window[0]:%Y-%m-%d %H:%M} 调整为 "
                f"{new_window[0]:%Y-%m-%d %H:%M}，"
                f"计划开始时间提前约 {advanced_duration}，请按新时间安排。"
            ),
            related_entity_type="task",
            related_entity_id=task.id,
            context_roles=["任务负责人"],
        )
    return sent


def notify_rescheduled_tasks_delayed(db, original_windows: dict[int, ScheduleWindow] | None, reason: str = "重新排程") -> int:
    if not original_windows:
        return 0
    new_windows = capture_task_schedule_windows(db, set(original_windows))
    sent = 0
    for task_id, original_window in original_windows.items():
        new_window = new_windows.get(task_id)
        if not new_window or new_window[0] <= original_window[0]:
            continue
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task or not task.assignee:
            continue
        project_code = task.project.code if task.project else ""
        task_label = f"{project_code} · {task.name}" if project_code else task.name
        delayed_duration = _format_time_change(
            (new_window[0] - original_window[0]).total_seconds(),
        )
        sent += push_by_rule(
            db, DELAYED_NOTIFICATION_RULE_TYPE, [task.assignee], "排程调整后，您的任务被动后移",
            f"因{reason}，您的任务“{task_label}”计划开始时间由 {original_window[0]:%Y-%m-%d %H:%M} 调整为 {new_window[0]:%Y-%m-%d %H:%M}，计划开始时间推迟约 {delayed_duration}，请按新时间安排。",
            related_entity_type="task", related_entity_id=task.id, context_roles=["任务负责人"],
        )
    return sent


def notify_advanced_task_assignees(
    db,
    completed_task: Task,
    completed_at: datetime,
    planned_end: datetime,
    moved_task_details: list[dict],
) -> int:
    if completed_at >= planned_end:
        return 0

    sent = 0
    for detail in moved_task_details:
        task = db.query(Task).filter(Task.id == detail["task_id"]).first()
        if not task or not task.assignee:
            continue
        project_code = task.project.code if task.project else ""
        task_label = f"{project_code} · {task.name}" if project_code else task.name
        sent += push_by_rule(
            db,
            ADVANCE_NOTIFICATION_RULE_TYPE,
            [task.assignee],
            "前序任务提前完成，您的任务已前移",
            (
                f"仪器前序任务“{completed_task.name}”已于 {completed_at:%Y-%m-%d %H:%M} "
                f"提前完成（原计划完成时间 {planned_end:%Y-%m-%d %H:%M}）。"
                f"您的任务“{task_label}”已由 "
                f"{detail['original_start']:%Y-%m-%d %H:%M}–"
                f"{detail['original_end']:%Y-%m-%d %H:%M} 前移至 "
                f"{detail['new_start']:%Y-%m-%d %H:%M}–"
                f"{detail['new_end']:%Y-%m-%d %H:%M}，请按新时间安排。"
            ),
            related_entity_type="task",
            related_entity_id=task.id,
            context_roles=["任务负责人"],
        )
    return sent
