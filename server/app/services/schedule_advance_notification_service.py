from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Task, TimeSlot
from app.services.push_notification_service import push_by_rule


ADVANCE_NOTIFICATION_RULE_TYPE = "task_schedule_advanced"
DELAYED_NOTIFICATION_RULE_TYPE = "task_schedule_delayed"
ScheduleWindow = tuple[datetime, datetime]
EXTERNAL_NOTIFICATION_LOOKAHEAD = timedelta(hours=48)
URGENT_TASK_STATUSES = {"blocked", "interrupted"}


def _should_deliver_externally(
    task: Task,
    new_window: ScheduleWindow,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now()
    if new_window[0] <= current + EXTERNAL_NOTIFICATION_LOOKAHEAD:
        return True
    if task.status in URGENT_TASK_STATUSES:
        return True
    project_end = task.project.end_date if task.project else None
    return bool(project_end and new_window[1] > project_end)


def _format_time_change(total_seconds: float) -> str:
    hours = max(0.1, round(abs(total_seconds) / 3600, 1))
    if hours > 24:
        days = round(hours / 24, 1)
        return f"{days:g} 天"
    return f"{hours:g} 小时"


def _format_window(window: ScheduleWindow) -> str:
    start, end = window
    date_label = f"{start.month}/{start.day}（周{'一二三四五六日'[start.weekday()]}）"
    duration = (end - start).total_seconds() / 3600
    return f"{date_label}{start:%H:%M}–{end:%H:%M}（{duration:g}小时）"


def _format_original_window(window: ScheduleWindow, change: str) -> str:
    start, end = window
    if start.date() == end.date():
        period = f"{start.month}/{start.day} {start:%H:%M}–{end:%H:%M}"
    else:
        period = f"{start.month}/{start.day}–{end.month}/{end.day}"
    return f"{period}（{change}）"


def _schedule_notification_content(
    task_label: str,
    new_window: ScheduleWindow,
    original_window: ScheduleWindow,
    change: str,
    reason: str,
) -> str:
    return (
        f"您的任务：{task_label}\n\n"
        f"新时间：{_format_window(new_window)}\n\n"
        f"原时间：{_format_original_window(original_window, change)}\n\n"
        f"原因：{reason}"
    )


def capture_task_schedule_windows(
    db,
    task_ids: set[int] | list[int],
) -> dict[int, ScheduleWindow]:
    ids = set(task_ids)
    if not ids:
        return {}

    # 必须只取现行时间槽。把历次被推翻的作废槽一起取最早开始、最晚结束，得到的
    # 不是某一版计划，而是所有版本的并集——一个 2.5 小时的任务会显示成横跨数天，
    # 排程影响弹窗里就成了「原计划 09-03 14:00–09-08 19:30」这种从未存在过的区间，
    # 看上去像是任务被莫名拉长，而它其实压根没动。
    slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id.in_(ids),
            TimeSlot.lifecycle_status == "active",
        )
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
        sent += push_by_rule(
            db,
            ADVANCE_NOTIFICATION_RULE_TYPE,
            [task.assignee],
            "任务前移通知",
            _schedule_notification_content(
                task_label, new_window, original_window, "已提前", f"排程调整：{reason}。",
            ),
            related_entity_type="task",
            related_entity_id=task.id,
            context_roles=["任务负责人"],
            external_delivery=_should_deliver_externally(task, new_window),
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
        sent += push_by_rule(
            db, DELAYED_NOTIFICATION_RULE_TYPE, [task.assignee], "任务后移通知",
            _schedule_notification_content(
                task_label, new_window, original_window, "已后移", f"排程调整：{reason}。",
            ),
            related_entity_type="task", related_entity_id=task.id, context_roles=["任务负责人"],
            external_delivery=_should_deliver_externally(task, new_window),
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
        completed_project_code = completed_task.project.code if completed_task.project else ""
        completed_label = (
            f"{completed_project_code} · {completed_task.name}"
            if completed_project_code else completed_task.name
        )
        sent += push_by_rule(
            db,
            ADVANCE_NOTIFICATION_RULE_TYPE,
            [task.assignee],
            "任务前移通知",
            _schedule_notification_content(
                task_label,
                (detail["new_start"], detail["new_end"]),
                (detail["original_start"], detail["original_end"]),
                "已提前",
                f"前序任务“{completed_label}”今日已提前完成。",
            ),
            related_entity_type="task",
            related_entity_id=task.id,
            context_roles=["任务负责人"],
            external_delivery=_should_deliver_externally(
                task,
                (detail["new_start"], detail["new_end"]),
                completed_at,
            ),
        )
    return sent
