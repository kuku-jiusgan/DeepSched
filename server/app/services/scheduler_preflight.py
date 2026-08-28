"""排程求解前的输入校验，以及指定仪器的候选收窄。

这些检查在建模之前完成：条件不满足就直接返回错误，不进入求解器。
"""

from __future__ import annotations

from app.services.project_hours_validation_service import (
    ProjectHoursExceededError,
    validate_projects_estimated_hours,
)


def validate_schedulable_input(
    db,
    *,
    tasks,
    instruments,
    current_project_id: int,
    allow_unassigned_human_task_ids: set[int] | None,
) -> dict | None:
    """输入不可排程时返回错误响应，可以排程时返回 None。"""
    allowed_unassigned = allow_unassigned_human_task_ids or set()
    unassigned_human_tasks = [
        task
        for task in tasks
        if task.requires_human
        and not task.assignee_id
        and task.id not in allowed_unassigned
    ]
    if unassigned_human_tasks:
        names = "、".join(task.name for task in unassigned_human_tasks[:3])
        suffix = "等" if len(unassigned_human_tasks) > 3 else ""
        return {
            "status": "error",
            "message": f"排程失败：人工任务【{names}{suffix}】未指定负责人。",
        }
    try:
        validate_projects_estimated_hours(db, {current_project_id})
    except ProjectHoursExceededError as exc:
        return {"status": "error", "message": str(exc)}
    if not instruments:
        return {"status": "error", "message": "没有可用仪器"}
    return None


def narrow_compat_to_fixed_instruments(compat, fixed_instrument_ids) -> dict | None:
    """局部重排要求保持原仪器时，把候选仪器收窄到指定的那一台。"""
    if fixed_instrument_ids:
        invalid_task_ids = []
        for task_id, instrument_id in fixed_instrument_ids.items():
            candidates = compat.get(task_id)
            if candidates is None:
                continue
            compat[task_id] = [
                instrument for instrument in candidates
                if instrument.id == instrument_id
            ]
            if not compat[task_id]:
                invalid_task_ids.append(task_id)
        if invalid_task_ids:
            return {
                "status": "error",
                "message": "局部重排任务原仪器当前不可用，无法保持原仪器排程",
            }
    return None
