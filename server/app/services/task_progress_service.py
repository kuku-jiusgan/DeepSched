from __future__ import annotations


def planned_task_minutes(task) -> int:
    """Return the task's authoritative planned workload, including approved delays."""
    base_minutes = max(0, round(float(task.est_duration_hours or 0) * 60))
    additional_minutes = max(0, int(getattr(task, "additional_planned_minutes", 0) or 0))
    return base_minutes + additional_minutes


def remaining_task_minutes(task) -> int:
    # 诊断层也会拿非 ORM 的任务对象调用这里，缺字段时按未执行处理。
    executed_minutes = int(getattr(task, "executed_minutes", None) or 0)
    return max(0, planned_task_minutes(task) - executed_minutes)


def planned_task_hours(task) -> float:
    return planned_task_minutes(task) / 60
