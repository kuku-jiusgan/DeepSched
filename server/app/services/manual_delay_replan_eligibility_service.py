from __future__ import annotations

from app.models import Project, Task, TimeSlot


REBUILDABLE_TASK_STATUSES = {"pending", "ready", "scheduled"}


def can_use_cp_sat_manual_delay_replan(db, task_ids: set[int]) -> bool:
    """Whether every affected task can be losslessly rebuilt by CP-SAT."""
    return manual_delay_replan_fallback_reasons(db, task_ids) == []


def manual_delay_replan_fallback_reasons(db, task_ids: set[int]) -> list[str]:
    """List explicit blockers for replacing a manual-delay closure."""
    reasons: set[str] = set()
    if not task_ids:
        return ["no_affected_tasks"]

    tasks = db.query(Task).filter(Task.id.in_(task_ids)).all()
    if len(tasks) != len(task_ids):
        reasons.add("missing_task")

    project_ids = {task.project_id for task in tasks}
    if None in project_ids:
        reasons.add("missing_project")
    elif db.query(Project.id).filter(Project.id.in_(project_ids)).count() != len(project_ids):
        reasons.add("missing_project")

    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
    ).all()
    if any(slot.status != "scheduled" for slot in slots):
        reasons.add("non_scheduled_slot")
    if any(slot.tier == "frozen" for slot in slots):
        reasons.add("frozen_slot")
    if any(
        slot.actual_start is not None or slot.actual_end is not None
        for slot in slots
    ):
        reasons.add("actual_execution_slot")

    for task in tasks:
        if task.status not in REBUILDABLE_TASK_STATUSES:
            reasons.add("non_rebuildable_task_status")
        if task.execution_segments:
            reasons.add("execution_history")
        if task.requires_human and task.assignee_id is None:
            reasons.add("missing_assignee")
    return sorted(reasons)
