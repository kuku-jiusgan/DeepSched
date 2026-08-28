"""方案签批通过后的排程落地：创建签批后任务、触发重排、记录结果。"""

from __future__ import annotations

from app.models import Task, TaskDependency, User
from app.schemas.approval_gate_schemas import ApprovalGateActionOut
from app.schemas.schemas import ProjectPlanInsertConfirmRequest
from app.services.approval_gate_access_service import (
    ensure_can_operate_gate,
    gate_or_404,
)
from app.services.approval_gate_errors import ApprovalGateInvalidError
from app.services.approval_gate_notification_service import notify_gate, record_gate_audit
from app.services.approval_gate_presentation_service import gate_out
from app.services.project_plan_template_service import POST_APPROVAL_STEPS
from app.services.task_dependency_service import create_continuous_successor


def create_post_approval_tasks(db, gate: Task) -> list[Task]:
    parent = gate.parent
    if parent is None:
        return []
    total = float(gate.project.estimated_hours or 0)
    specs = [
        (name, task_type, total * float(percentage), requires_instrument)
        for name, task_type, percentage, requires_instrument in POST_APPROVAL_STEPS
    ]
    created = []
    for offset, (name, task_type, hours, requires_instrument) in enumerate(specs, start=3):
        task = Task(project_id=gate.project_id, parent_id=parent.id, name=name, task_type=task_type,
                    requires_instrument=requires_instrument, requires_human=True,
                    est_duration_hours=hours, switchover_hours=0, assignee_id=gate.assignee_id,
                    status="pending", schedule_dirty=True, instrument_ids=[] if not requires_instrument else [])
        db.add(task)
        db.flush()
        created.append(task)
    db.add(TaskDependency(task_id=created[0].id, predecessor_id=gate.id))
    db.add(create_continuous_successor(created[0], created[1]))
    db.flush()
    return created

def apply_gate_schedule(db, gate: Task, is_forecast: bool):
    from app.services.project_plan_apply_service import (
        apply_project_plan,
        confirm_project_plan_insert,
    )
    from app.services.approval_gate_schedule_context import build_approval_schedule_context

    approval_context = build_approval_schedule_context(db, gate)
    result = apply_project_plan(
        db,
        gate.project_id,
        approval_context=approval_context,
    )
    # 预计签批和正式签批都直接落地下游排程，不把跨项目影响确认暴露给前端。
    if result.status == "insert_confirmation_required" and result.preview_token:
        result = confirm_project_plan_insert(
            db,
            ProjectPlanInsertConfirmRequest(
                project_id=gate.project_id,
                preview_token=result.preview_token,
            ),
            approval_context=approval_context,
        )
    gate = gate_or_404(db, gate.id)
    store_schedule_result(db, gate, result, is_forecast)
    db.commit()
    return result

def store_schedule_result(db, gate: Task, result, is_forecast: bool) -> None:
    if result.status == "applied":
        gate.approval_schedule_status = "forecast" if is_forecast else "applied"
    elif result.status == "insert_confirmation_required":
        gate.approval_schedule_status = "confirmation_required"
    else:
        gate.approval_schedule_status = "deadline_risk"
    gate.approval_schedule_message = result.message
    gate.approval_preview_token = result.preview_token
    gate.approval_schedule_run_id = result.schedule_run_id
    gate.approval_moved_tasks = result.moved_tasks
    if not is_forecast:
        if gate.approval_schedule_status == "applied":
            content = f"项目【{gate.project.code}】客户方案已同意，后续验证排程已自动更新。"
        elif gate.approval_schedule_status == "confirmation_required":
            content = f"项目【{gate.project.code}】客户方案已同意，需要确认跨项目排程影响。"
        else:
            content = f"项目【{gate.project.code}】客户方案已同意，但当前无法在结题日期前完成。"
        notify_gate(db, gate, "approval_schedule_result", "签批后排程结果", content)

def confirm_approval_schedule(db, gate_id: int, preview_token: str, user: User) -> ApprovalGateActionOut:
    gate = gate_or_404(db, gate_id)
    ensure_can_operate_gate(gate, user)
    if gate.approval_schedule_status != "confirmation_required":
        raise ApprovalGateInvalidError("当前签批没有待确认的跨项目排程")
    from app.services.project_plan_apply_service import confirm_project_plan_insert
    from app.services.approval_gate_schedule_context import build_approval_schedule_context

    result = confirm_project_plan_insert(db, ProjectPlanInsertConfirmRequest(
        project_id=gate.project_id,
        preview_token=preview_token,
    ), approval_context=build_approval_schedule_context(db, gate))
    store_schedule_result(db, gate, result, is_forecast=False)
    record_gate_audit(db, user, "approval_schedule_impact_confirmed", gate, {
        "schedule_run_id": result.schedule_run_id,
        "moved_tasks": result.moved_tasks,
    })
    db.commit()
    return ApprovalGateActionOut(
        gate=gate_out(db, gate, user),
        schedule_status=gate.approval_schedule_status or "applied",
        schedule_message=result.message,
    )
