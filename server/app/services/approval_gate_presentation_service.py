"""把签批节点 Task 组装成对外的 ApprovalGateOut。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.task_status import resolve_task_execution_status
from app.models import Task, TaskDependency, TimeSlot, User
from app.schemas.approval_gate_schemas import ApprovalGateOut, ApprovalGateTaskRef
from app.services.approval_gate_access_service import can_operate_gate_task
from app.services.approval_gate_graph_service import task_completed_at
from app.services.approval_gate_schedule_context import approval_top_level_task_name
from app.services.user_role_service import has_role


def gate_out(db, gate: Task, user: User) -> ApprovalGateOut:
    project = gate.project
    predecessor_tasks = [
        ApprovalGateTaskRef(
            id=dependency.predecessor.id,
            name=dependency.predecessor.name,
            status=resolve_task_execution_status(dependency.predecessor),
            completed_at=task_completed_at(dependency.predecessor),
        )
        for dependency in gate.predecessors
    ]
    unlock_dependencies = db.query(TaskDependency).filter(
        TaskDependency.predecessor_id == gate.id,
    ).all()
    scheduled_unlock_ids = _scheduled_task_ids(
        db, {dependency.task_id for dependency in unlock_dependencies},
    )
    unlock_tasks = [
        ApprovalGateTaskRef(
            id=dependency.task.id,
            name=dependency.task.name,
            status=resolve_task_execution_status(dependency.task),
            est_duration_hours=dependency.task.est_duration_hours,
            requires_instrument=bool(dependency.task.requires_instrument),
            instrument_ids=list(dependency.task.instrument_ids or []),
            is_scheduled=dependency.task.id in scheduled_unlock_ids,
        )
        for dependency in unlock_dependencies
    ]
    latest_approval_at = latest_approval_deadline(db, gate, unlock_tasks)
    expected = gate.expected_approval_at
    risk_status = "normal"
    now = datetime.now()
    if gate.gate_status != "approved" and expected and expected < now:
        risk_status = "overdue"
    elif gate.gate_status != "approved" and latest_approval_at and expected and expected > latest_approval_at:
        risk_status = "deadline_risk"
    elif gate.gate_status != "approved" and expected and expected <= now + timedelta(days=2):
        risk_status = "upcoming"
    project_slots = [
        slot for task in project.tasks for slot in task.time_slots
        if slot.status in {"scheduled", "running", "paused", "blocked", "interrupted", "completed"}
    ]
    expected_completion = max((slot.plan_end for slot in project_slots), default=None)
    return ApprovalGateOut(
        id=gate.id,
        project_id=project.id,
        project_code=project.code,
        project_name=project.name,
        client_name=project.client_name,
        project_manager_id=project.manager_id,
        project_manager_name=project.manager.display_name if project.manager else None,
        assignee_id=gate.assignee_id,
        assignee_name=gate.assignee.display_name if gate.assignee else None,
        project_end_date=project.end_date,
        name=gate.name,
        top_level_task_name=approval_top_level_task_name(db, gate),
        gate_status=gate.gate_status or "not_submitted",
        expected_approval_at=gate.expected_approval_at,
        submitted_at=gate.submitted_at,
        approved_at=gate.approved_at,
        approved_by_name=gate.approved_by_user.display_name if gate.approved_by_user else None,
        approval_note=gate.approval_note,
        predecessor_tasks=predecessor_tasks,
        unlock_tasks=unlock_tasks,
        latest_approval_at=latest_approval_at,
        risk_status=risk_status,
        schedule_status=gate.approval_schedule_status,
        schedule_message=gate.approval_schedule_message,
        schedule_run_id=gate.approval_schedule_run_id,
        preview_token=gate.approval_preview_token,
        moved_tasks=gate.approval_moved_tasks or 0,
        project_expected_completion=expected_completion,
        can_operate=(
            can_operate_gate_task(gate, user)
        ) and not has_role(user, "项目管理员"),
    )

def _scheduled_task_ids(db, task_ids: set[int]) -> set[int]:
    """已经有活跃时间槽的任务。签批通过后它们会正常排入，就不再算待排工时。"""
    if not task_ids:
        return set()
    rows = db.query(TimeSlot.task_id).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
    ).distinct().all()
    return {row[0] for row in rows}


def latest_approval_deadline(db, gate: Task, unlock_tasks: list[ApprovalGateTaskRef]) -> datetime | None:
    if not gate.project.end_date:
        return None
    project_tasks = {task.id: task for task in gate.project.tasks if not task.is_external_gate}
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(set(project_tasks))
    ).all()
    downstream: dict[int, list[int]] = {}
    for dependency in dependencies:
        downstream.setdefault(dependency.predecessor_id, []).append(dependency.task_id)

    def critical_hours(task_id: int, visiting: set[int]) -> float:
        if task_id in visiting or task_id not in project_tasks:
            return 0
        task = project_tasks[task_id]
        own = float(task.est_duration_hours or 0) + float(task.switchover_hours or 0)
        child_hours = [critical_hours(child_id, visiting | {task_id}) for child_id in downstream.get(task_id, [])]
        return own + max(child_hours, default=0)

    hours = max((critical_hours(task.id, set()) for task in unlock_tasks), default=0)
    return gate.project.end_date - timedelta(days=hours / 8)

def naive_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is None else value.replace(tzinfo=None)
