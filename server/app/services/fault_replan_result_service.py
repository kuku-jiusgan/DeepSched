from __future__ import annotations

from datetime import datetime

from app.models import Task, TimeSlot


def build_fault_impact_details(
    db,
    task_ids: set[int],
    original_windows: dict[int, tuple[datetime, datetime]],
) -> list[dict]:
    if not task_ids:
        return []
    tasks = {task.id: task for task in db.query(Task).filter(Task.id.in_(task_ids)).all()}
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(("scheduled", "running", "paused", "blocked", "interrupted")),
    ).order_by(TimeSlot.task_id, TimeSlot.plan_start).all()
    grouped: dict[int, list[TimeSlot]] = {}
    for slot in slots:
        grouped.setdefault(slot.task_id, []).append(slot)
    details = []
    for task_id, task in tasks.items():
        task_slots = grouped.get(task_id, [])
        if not task_slots or task_id not in original_windows:
            continue
        original_start, original_end = original_windows[task_id]
        shifted_start = min(slot.plan_start for slot in task_slots)
        shifted_end = max(slot.plan_end for slot in task_slots)
        can_shift = not task.project or not task.project.end_date or shifted_end <= task.project.end_date
        reason = "" if can_shift else (
            f"顺延后超过项目【{task.project.name}】结束日期，任务【{task.name}】存在超期风险。"
        )
        details.append({
            "task_id": task.id,
            "task_name": task.name,
            "project_id": task.project_id,
            "project_name": task.project.name if task.project else None,
            "project_code": task.project.code if task.project else None,
            "assignee_name": task.assignee.display_name if task.assignee else None,
            "original_start": original_start.isoformat(),
            "original_end": original_end.isoformat(),
            "shifted_start": shifted_start.isoformat(),
            "shifted_end": shifted_end.isoformat(),
            "can_shift": can_shift,
            "reason": reason,
        })
    return sorted(details, key=lambda item: (item["original_start"], item["task_id"]))
