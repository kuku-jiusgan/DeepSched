"""方案签批的写入流程：创建签批节点、提交客户、记录签批通过。

签批相关的其它职责拆分在同名前缀的模块中：
- approval_gate_errors: 领域异常
- approval_gate_access_service: 实体查找与操作权限
- approval_gate_graph_service: 任务依赖图遍历与下游时间槽清理
- approval_gate_presentation_service: ApprovalGateOut 组装
- approval_gate_query_service: 列表查询与分页
- approval_gate_schedule_service: 签批后的排程落地
- approval_gate_notification_service: 审计留痕与通知推送
"""

from __future__ import annotations

from datetime import datetime

from app.models import Task, TaskDependency, User
from app.domain.errors import DomainConflictError
from app.schemas.approval_gate_schemas import (
    ApprovalGateActionOut,
    ApprovalGateCreate,
    ApprovalGateOut,
    ApprovalGateSubmit,
)
from app.services.approval_gate_access_service import (
    ensure_can_operate_gate,
    ensure_can_operate_project,
    gate_or_404,
    project_or_404,
    resolve_gate_assignee_id,
)
from app.services.approval_gate_errors import (
    ApprovalGateInvalidError,
    ApprovalGateNotFoundError,
    ApprovalGatePermissionError,
)
from app.services.approval_gate_graph_service import (
    clear_descendant_slots,
    descendant_tasks,
    downstream_ids,
    ensure_gate_predecessors_completed,
    unapproved_gate_context,
)
from app.services.approval_gate_notification_service import (
    notify_gate,
    record_gate_audit,
    scan_approval_deadlines,
)
from app.services.approval_gate_presentation_service import gate_out, naive_datetime
from app.services.approval_gate_query_service import (
    get_approval_gate,
    list_approval_gates,
)
from app.services.approval_gate_schedule_service import (
    apply_gate_schedule,
    confirm_approval_schedule,
    create_post_approval_tasks,
)
from app.services.task_delay_status_service import reset_task_delay
from app.services.project_plan_apply_service import ProjectPlanInvalidError
from app.services.schedule_run_lock_service import ScheduleBusyError
from app.services.schedule_request_service import enqueue_schedule_request


# 路由层与既有调用方统一从本模块导入签批能力，拆分后保持入口不变。
__all__ = [
    "ApprovalGateInvalidError",
    "ApprovalGateNotFoundError",
    "ApprovalGatePermissionError",
    "approve_approval_gate",
    "confirm_approval_schedule",
    "create_approval_gate",
    "get_approval_gate",
    "list_approval_gates",
    "scan_approval_deadlines",
    "submit_approval_gate",
    "unapproved_gate_context",
]


def create_approval_gate(db, project_id: int, data: ApprovalGateCreate, user: User) -> ApprovalGateOut:
    project = project_or_404(db, project_id)
    ensure_can_operate_project(project, user)
    assignee_id = resolve_gate_assignee_id(db, project, data.assignee_id)
    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    task_by_id = {task.id: task for task in tasks}
    predecessor = task_by_id.get(data.predecessor_task_id)
    unlock_tasks = [task_by_id.get(task_id) for task_id in sorted(set(data.unlock_task_ids))]
    if not predecessor or any(task is None for task in unlock_tasks):
        raise ApprovalGateInvalidError("前置任务或解锁任务不属于当前项目")
    if predecessor.is_external_gate or any(task.is_external_gate for task in unlock_tasks):
        raise ApprovalGateInvalidError("方案签批只能连接普通项目任务")
    if predecessor.id in downstream_ids(db, {task.id for task in unlock_tasks if task}):
        raise ApprovalGateInvalidError("方案签批会形成循环依赖")
    affected_ids = downstream_ids(db, {task.id for task in unlock_tasks if task})
    affected_tasks = [task_by_id[task_id] for task_id in affected_ids if task_id in task_by_id]
    protected = [task for task in affected_tasks if task.schedule_lock_status != "none"]
    if protected:
        names = "、".join(task.name for task in protected[:3])
        raise ApprovalGateInvalidError(f"下游任务【{names}】已冻结、运行或完成，不能增加方案签批")

    gate = Task(
        project_id=project_id,
        name=data.name.strip() or "方案签批",
        task_type="approval_gate",
        requires_instrument=False,
        requires_human=False,
        est_duration_hours=None,
        switchover_hours=0,
        assignee_id=assignee_id,
        status="waiting_external",
        is_external_gate=True,
        gate_status="not_submitted",
        schedule_dirty=False,
    )
    db.add(gate)
    db.flush()
    db.add(TaskDependency(task_id=gate.id, predecessor_id=predecessor.id))
    for task in unlock_tasks:
        db.add(TaskDependency(task_id=task.id, predecessor_id=gate.id))
    clear_descendant_slots(db, affected_ids)
    for task in affected_tasks:
        task.status = "waiting_external"
        task.schedule_dirty = False
    record_gate_audit(db, user, "approval_gate_created", gate, {
        "predecessor_task_id": predecessor.id,
        "unlock_task_ids": [task.id for task in unlock_tasks],
    })
    notify_gate(db, gate, "approval_pending", "方案待提交客户", f"项目【{project.code}】已增加方案签批，请在方案完成后提交客户。")
    db.commit()
    db.refresh(gate)
    return gate_out(db, gate, user)

def submit_approval_gate(
    db,
    gate_id: int,
    data: ApprovalGateSubmit,
    user: User,
) -> ApprovalGateActionOut:
    gate = gate_or_404(db, gate_id)
    ensure_can_operate_gate(gate, user)
    if gate.gate_status == "approved":
        raise ApprovalGateInvalidError("该方案已经签批")
    expected_at = naive_datetime(data.expected_approval_at)
    if expected_at <= datetime.now():
        raise ApprovalGateInvalidError("预计签批完成时间必须晚于当前时间")
    gate.gate_status = "waiting_approval"
    gate.status = "waiting_approval"
    gate.submitted_at = gate.submitted_at or datetime.now()
    gate.expected_approval_at = expected_at
    gate.approval_note = data.approval_note
    record_gate_audit(db, user, "approval_gate_submitted", gate, {
        "expected_approval_at": expected_at.isoformat(),
    })
    # 签批后的任务在通过前不进入排程，提交阶段只登记预计签批时间。
    # 这段工时由 project_pending_workload_service 计入交付预测，
    # 提前把延期风险暴露出来。
    gate.approval_schedule_status = "pending_approval"
    gate.approval_schedule_message = "方案已提交，签批通过后生成后续排程"
    db.commit()
    return ApprovalGateActionOut(
        gate=gate_out(db, gate, user),
        schedule_status=gate.approval_schedule_status,
        schedule_message=gate.approval_schedule_message,
        preview_token=None,
    )

def approve_approval_gate(db, gate_id: int, note: str | None, user: User) -> ApprovalGateActionOut:
    gate = gate_or_404(db, gate_id)
    ensure_can_operate_gate(gate, user)
    ensure_gate_predecessors_completed(gate)
    if gate.gate_status not in {"not_submitted", "waiting_approval"}:
        raise ApprovalGateInvalidError("该方案已经完成签批")
    previous_status = gate.gate_status
    previous_task_status = gate.status
    approved_at = datetime.now()
    gate.gate_status = "approved"
    gate.status = "completed"
    gate.submitted_at = gate.submitted_at or approved_at
    gate.approved_at = approved_at
    gate.approved_by = user.id
    if note is not None:
        gate.approval_note = note
    downstream = descendant_tasks(db, gate.id)
    if not downstream:
        downstream = create_post_approval_tasks(db, gate)
    for task in downstream:
        if not task.is_external_gate and task.schedule_lock_status == "none":
            task.status = "pending"
            reset_task_delay(task)
            task.schedule_dirty = True
    # 签批和后续排程必须在同一个事务里完成。若排程锁被占用、求解失败或
    # 没有生成任何时间槽，不能留下“已签批但未排程”的半成品状态。
    try:
        result = apply_gate_schedule(db, gate, is_forecast=False, commit=False)
    except ScheduleBusyError as exc:
        request_id = _queue_approval_gate(db, gate_id, previous_status, previous_task_status, note, user)
        return ApprovalGateActionOut(
            gate=gate_out(db, gate_or_404(db, gate_id), user),
            schedule_status="queued",
            schedule_message="排程计算正在进行中，签批已进入排程队列",
            request_id=request_id,
        )
    except (DomainConflictError, ProjectPlanInvalidError) as exc:
        _record_schedule_failure(
            db, gate_id, previous_status, previous_task_status, str(exc), user,
        )
        raise ApprovalGateInvalidError(
            f"签批未完成，后续排程失败：{exc}",
        ) from exc
    if result.status != "applied":
        message = result.message or "未生成后续排程"
        _record_schedule_failure(
            db, gate_id, previous_status, previous_task_status, message, user,
        )
        raise ApprovalGateInvalidError(f"签批未完成，后续排程失败：{message}")
    record_gate_audit(db, user, "approval_gate_approved", gate, {
        "approved_at": gate.approved_at.isoformat(),
        "direct_confirmation": previous_status == "not_submitted",
    })
    db.commit()
    return ApprovalGateActionOut(
        gate=gate_out(db, gate, user),
        schedule_status=gate.approval_schedule_status or result.status,
        schedule_message=result.message,
        preview_token=gate.approval_preview_token,
    )


def _record_schedule_failure(
    db,
    gate_id: int,
    previous_gate_status: str,
    previous_task_status: str | None,
    message: str,
    user: User,
) -> None:
    """回滚试排改动，只保留可重试的签批失败状态。"""
    db.rollback()
    gate = gate_or_404(db, gate_id)
    gate.gate_status = previous_gate_status
    gate.status = previous_task_status
    gate.approved_at = None
    gate.approved_by = None
    gate.approval_schedule_status = "schedule_failed"
    gate.approval_schedule_message = f"签批未完成，后续排程失败：{message}"
    record_gate_audit(db, user, "approval_gate_schedule_failed", gate, {
        "reason": message,
        "gate_status": previous_gate_status,
    })
    db.commit()


def _queue_approval_gate(
    db, gate_id: int, previous_gate_status: str, previous_task_status: str | None,
    note: str | None, user: User,
) -> str:
    """锁冲突时撤销试批改动，持久化队列请求，成功后再完成签批。"""
    db.rollback()
    gate = gate_or_404(db, gate_id)
    gate.gate_status = previous_gate_status
    gate.status = previous_task_status
    gate.approved_at = None
    gate.approved_by = None
    gate.approval_schedule_status = "queued"
    gate.approval_schedule_message = "排程计算正在进行中，签批已进入排程队列"
    record_gate_audit(db, user, "approval_gate_schedule_queued", gate, {})
    db.commit()
    request = enqueue_schedule_request(
        db,
        gate.project_id,
        user.id,
        request_type="approval_gate",
        priority=20,
        payload={
            "project_id": gate.project_id,
            "gate_id": gate.id,
            "requested_by": user.id,
            "note": note,
        },
    )
    return request.id
