from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Callable

from app.models import Project, Task, TimeSlot
from app.services.approval_gate_schedule_context import ApprovalScheduleContext
from app.services.schedule_insert_resources import resource_queue_task_ids


def load_approval_resource_queue_tasks(
    db,
    project: Project,
    selected_tasks: list[Task],
    approval_context: ApprovalScheduleContext | None,
    task_has_protected_slot: Callable[[object, int], bool],
) -> list[Task]:
    if not approval_context or not approval_context.anchor_at:
        return []
    excluded_ids = {task.id for task in selected_tasks}
    queue_ids = resource_queue_task_ids(
        db,
        selected_tasks,
        approval_context.anchor_at,
        excluded_ids,
    )
    if not queue_ids:
        return []
    project_priority = int(project.priority or 3)
    tasks = db.query(Task).join(Project).filter(
        Task.id.in_(queue_ids),
        Project.priority >= project_priority,
    ).all()
    return [
        task for task in tasks
        if task.id not in approval_context.downstream_task_ids
        and task.status in {"pending", "scheduled", "blocked"}
        and not task_has_protected_slot(db, task.id)
    ]


def approval_earliest_bounds(
    approval_context: ApprovalScheduleContext | None,
) -> dict[int, datetime]:
    if not approval_context or not approval_context.anchor_at:
        return {}
    return {
        task_id: approval_context.anchor_at
        for task_id in approval_context.downstream_task_ids
    }


def apply_success_message(
    approval_context: ApprovalScheduleContext | None,
    moved: bool,
) -> str:
    if approval_context:
        return (
            "签批后任务已插入到当前顶级任务后"
            if moved else "签批后任务已插入到当前顶级任务后，未顺延其他任务"
        )
    return "排程完成"


def plan_fingerprint(
    db,
    project: Project,
    tasks: list[Task],
    approval_context: ApprovalScheduleContext | None = None,
) -> str:
    unique_tasks = sorted(
        {task.id: task for task in tasks}.values(),
        key=lambda task: (task.project_id, task.created_at, task.id),
    )
    slots = db.query(TimeSlot).filter(TimeSlot.status.in_([
        "scheduled", "running", "completed", "paused", "blocked", "interrupted",
    ])).order_by(TimeSlot.id).all()
    payload = {
        "project": [
            project.id,
            project.priority,
            _iso(project.start_date),
            _iso(project.end_date),
            _iso(project.updated_at),
        ],
        "tasks": [
            [
                task.id,
                task.project_id,
                int(task.project.priority or 3) if task.project else 3,
                task.status,
                bool(task.schedule_dirty),
                task.task_type,
                task.est_duration_hours,
                task.switchover_hours,
                bool(task.allow_split),
                bool(task.allow_transfer),
                task.milestone_id,
                task.priority_weight,
                sorted(task.instrument_ids or []),
                sorted(task.predecessor_ids),
                task.parent_id,
                bool(task.is_external_gate),
                task.gate_status,
                _iso(task.expected_approval_at),
                _iso(task.approved_at),
                _iso(task.updated_at),
            ]
            for task in unique_tasks
        ],
        "slots": [
            [
                slot.id,
                slot.task_id,
                slot.instrument_id,
                _iso(slot.plan_start),
                _iso(slot.plan_end),
                slot.tier,
                slot.status,
                _iso(slot.updated_at),
            ]
            for slot in slots
        ],
        "approval_context": [
            approval_context.gate_id,
            sorted(approval_context.downstream_task_ids),
            sorted(approval_context.branch_task_ids),
            _iso(approval_context.anchor_at),
        ] if approval_context else None,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
