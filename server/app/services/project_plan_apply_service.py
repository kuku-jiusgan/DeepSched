from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Project, Task, TaskDependency, TimeSlot
from app.schemas.schemas import ProjectPlanApplyResponse, ProjectPlanInsertConfirmRequest
from app.services.project_hours_validation_service import (
    ProjectHoursExceededError,
    validate_project_estimated_hours,
)
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.approval_gate_schedule_context import ApprovalScheduleContext
from app.services.project_plan_apply_helpers import (
    approval_earliest_bounds,
    apply_success_message,
    clear_replanned_project_dirty,
    expand_movable_downstream_tasks,
    load_approval_resource_queue_tasks,
    plan_fingerprint,
)
from app.services.scheduler_persistence import ACTIVE_EXECUTION_STATUSES
from app.services.task_delay_status_service import reset_task_delay
from app.services.schedule_priority_dependency_service import build_schedule_priority_dependencies
from app.services.schedule_priority_dependency_service import _tasks_with_unfinished_predecessors
from app.services.approval_gate_schedule_dependencies import (
    build_approval_insert_dependencies,
)
from app.services.schedule_slot_protection_service import (
    task_has_immovable_slot,
    tasks_with_immovable_slot,
)
from app.services.project_plan_impact_service import (
    build_project_impacts as _build_project_impacts,
    project_completions as _project_completions,
    project_impact_message as _project_impact_message,
)
from app.services.project_pending_workload_service import (
    pending_approval_workload as _pending_approval_workload,
)
from app.services.project_instrument_validation_service import (
    RequiredInstrumentError,
    validate_required_task_instruments,
)
from app.services.project_task_rollup_service import recalculate_project_parent_hours
from app.services.schedule_insert_service import (
    _build_impacts,
    _selected_instrument_ids,
    _task_windows,
)
from app.services.schedule_resource_closure_service import load_resource_closure_movable_tasks
from app.services.project_plan_errors import ProjectPlanInvalidError, ProjectPlanNotFoundError
from app.services.project_plan_feasibility_service import validate_immediate_approval_feasibility

MOVABLE_TIERS = ["confirmed", "forecast"]
MOVABLE_SLOT_STATUSES = ["scheduled", "paused", "blocked", "interrupted"]
def apply_project_plan(
    db,
    project_id: int,
    approval_context: ApprovalScheduleContext | None = None,
    preserve_existing: bool = False,
) -> ProjectPlanApplyResponse:
    recalculate_project_parent_hours(db, project_id)
    db.flush()
    try:
        validate_project_estimated_hours(db, project_id)
    except ProjectHoursExceededError as exc:
        raise ProjectPlanInvalidError(str(exc))
    project_tasks = db.query(Task).filter(Task.project_id == project_id).all()
    try:
        validate_required_task_instruments(project_tasks)
    except RequiredInstrumentError as exc:
        raise ProjectPlanInvalidError(str(exc))
    project, selected_tasks = _load_project_candidates(db, project_id)
    if approval_context:
        selected_tasks = [
            task for task in selected_tasks
            if task.id in approval_context.downstream_task_ids
        ]
    if not selected_tasks:
        return ProjectPlanApplyResponse(
            status="no_changes",
            message="当前项目没有需要重新排程的任务",
            project_id=project_id,
        )

    # 先确定资源队列，再进行一次权威求解。原流程先试排当前任务，
    # 成功后又对完整队列求解，导致一次请求最多消耗两个 30 秒求解预算。
    movable_tasks = _load_insert_movable_tasks(
        db, project, selected_tasks, approval_context,
    )
    if not movable_tasks:
        return _execute_replan(
            db, project, selected_tasks, [], commit=not preserve_existing,
            approval_context=approval_context, use_savepoint=preserve_existing,
        )
    return _preview_plan_insert(
        db, project, selected_tasks, movable_tasks, approval_context, preserve_existing,
    )


def confirm_project_plan_insert(
    db,
    data: ProjectPlanInsertConfirmRequest,
    approval_context: ApprovalScheduleContext | None = None,
    preserve_existing: bool = False,
    verify_preview_token: bool = True,
) -> ProjectPlanApplyResponse:
    project, selected_tasks = _load_project_candidates(db, data.project_id)
    if not selected_tasks:
        raise ProjectPlanInvalidError("计划已经更新，请重新计算排程")
    if approval_context:
        selected_tasks = [
            task for task in selected_tasks
            if task.id in approval_context.downstream_task_ids
        ]
    if not selected_tasks:
        raise ProjectPlanInvalidError("计划已经更新，请重新计算排程")
    movable_tasks = _load_insert_movable_tasks(
        db, project, selected_tasks, approval_context,
    )
    if not movable_tasks:
        raise ProjectPlanInvalidError("当前没有允许移动的低优先级任务")
    if verify_preview_token:
        preview_token = plan_fingerprint(
            db, project, selected_tasks + movable_tasks, approval_context,
        )
        if preview_token != data.preview_token:
            raise ProjectPlanInvalidError("计划或排程数据已变化，请重新计算影响")
    result = _execute_replan(
        db, project, selected_tasks, movable_tasks, commit=not preserve_existing,
        approval_context=approval_context,
    )
    if result.status != "applied":
        db.rollback()
        raise ProjectPlanInvalidError(result.message or "插单排程失败")
    return result


def _preview_plan_insert(
    db,
    project: Project,
    selected_tasks: list[Task],
    movable_tasks: list[Task] | None = None,
    approval_context: ApprovalScheduleContext | None = None,
    preserve_existing: bool = False,
) -> ProjectPlanApplyResponse:
    movable_tasks = movable_tasks if movable_tasks is not None else _load_insert_movable_tasks(
        db, project, selected_tasks, approval_context,
    )
    if not movable_tasks:
        return ProjectPlanApplyResponse(
            status="error",
            message="没有可移动的低优先级任务，无法完成重排",
            project_id=project.id,
        )
    preview_token = plan_fingerprint(
        db, project, selected_tasks + movable_tasks, approval_context,
    )
    preview_savepoint = db.begin_nested()
    preview = _execute_replan(
        db, project, selected_tasks, movable_tasks, commit=False,
        approval_context=approval_context,
        rollback_on_failure=False,
    )
    if preview.status != "applied":
        preview_savepoint.rollback()
        return ProjectPlanApplyResponse(
            status="error",
            message=preview.message or "插单模拟失败",
            project_id=project.id,
            schedule_failure=preview.schedule_failure,
        )
    if preview.moved_tasks == 0:
        preview_savepoint.commit()
        preview.message = (
            apply_success_message(approval_context, moved=False)
            if approval_context else "排程完成，未顺延其他任务"
        )
        if not preserve_existing:
            db.commit()
        return preview
    preview_savepoint.rollback()
    return ProjectPlanApplyResponse(
        status="insert_confirmation_required",
        message=_project_impact_message(preview.project_impacts),
        project_id=project.id,
        schedule_run_id=preview.schedule_run_id,
        timeslots_created=preview.timeslots_created,
        moved_tasks=preview.moved_tasks,
        conflicts_checked=preview.conflicts_checked,
        preview_token=preview_token,
        impacts=preview.impacts,
        project_impacts=preview.project_impacts,
    )


def _execute_replan(
    db,
    project: Project,
    selected_tasks: list[Task],
    movable_tasks: list[Task],
    commit: bool,
    approval_context: ApprovalScheduleContext | None = None,
    use_savepoint: bool = False,
    retain_changes: bool = True,
    rollback_on_failure: bool = True,
) -> ProjectPlanApplyResponse:
    savepoint = db.begin_nested() if use_savepoint else None
    replan_tasks = _unique_tasks(selected_tasks + movable_tasks)
    replan_task_ids = {task.id for task in replan_tasks}
    selected_task_ids = {task.id for task in selected_tasks}
    old_windows = _task_windows(db, replan_task_ids)
    moved_project_ids = {task.project_id for task in movable_tasks}
    # 签批后未排的工时在重排前后都不变，但它会把被顺延项目的真实完工时间
    # 推到结题日之后，必须一并计入 exceeds_end_date 的判定。
    moved_workloads = _pending_approval_workload(db, moved_project_ids)
    old_project_completions = _project_completions(db, moved_project_ids, moved_workloads)

    # 时间槽必须在求解前删除，否则求解器会把它们当成不可移动的占用。但排程
    # 失败后的占用明细还要按原计划位置统计这些工时，删掉就只剩"没有时间槽"
    # 的任务，工时会被记进预测工时列，仪器占用凭空变成 0。先留一份快照。
    released_slots = _released_slot_intervals(db, replan_task_ids)
    # 这些槽本次要让位。原先的表达方式是求解前先把它们删掉，让求解器回头查库时
    # 自然看不到——"一个假设"的最小单元于是成了一个事务而不是一个值。现在它就是
    # 一个槽号集合：求解时排除，作废挪到写回阶段作为指令执行。
    released_slot_ids = {
        slot_id for (slot_id,) in _movable_slots_query(
            db, replan_task_ids,
        ).with_entities(TimeSlot.id).all()
    }

    try:
        validate_immediate_approval_feasibility(
            db,
            project=project,
            replan_tasks=replan_tasks,
            released_slot_ids=released_slot_ids,
        )
    except Exception:
        if savepoint:
            savepoint.rollback()
        elif rollback_on_failure:
            db.rollback()
        raise
    # 只释放可移动时间槽，不要在求解前改任务状态。求解器需要看到任务原本的
    # 执行状态，才能正确判断已开始/已完成工作和资源占用；排程成功后的最终状态
    # 由持久化层统一写回。暂停/进行中的任务也允许被顺延，但不能在这里被重置。
    preserved_status_task_ids = {
        task.id for task in replan_tasks if task.status in ACTIVE_EXECUTION_STATUSES
    }
    for task in replan_tasks:
        # 新增/尚未排程的任务仍需进入 pending；已有活动时间槽的任务保留原状态，
        # 否则会改变求解器对已排程资源和执行进度的判断。
        has_active_slot = any(
            slot.lifecycle_status == "active" for slot in task.time_slots
        )
        if task.id not in preserved_status_task_ids and not has_active_slot:
            task.status = "pending"
        reset_task_delay(task)
    db.flush()

    from app.services.scheduler import SchedulerService

    solver_result = SchedulerService(db).generate(
        task_ids=sorted(replan_task_ids),
        mode="insert" if movable_tasks else "normal",
        commit=False,
        original_schedule_windows=old_windows,
        additional_dependencies=(
            build_approval_insert_dependencies(db, selected_tasks, movable_tasks)
            if approval_context
            else build_schedule_priority_dependencies(
                db, project, selected_tasks, movable_tasks,
            )
        ),
        earliest_start_bounds=approval_earliest_bounds(approval_context),
        advance_notification_reason="项目任务保存重排",
        emit_advance_notifications=commit,
        early_start_task_ids=(
            approval_context.downstream_task_ids if approval_context else None
        ),
        current_project_id=project.id,
        rollback_on_conflict=rollback_on_failure and not use_savepoint,
        released_slot_intervals=released_slots,
        released_slot_ids=released_slot_ids,
        # 已排程的可移动任务必须显式载入，否则旧槽虽从求解中排除，
        # 落盘任务集合却无法生成对应的 SupersedeSlot。
        replaceable_task_ids=replan_task_ids,
        preserved_status_task_ids=preserved_status_task_ids,
    )
    if solver_result.get("status") != "ok":
        if savepoint:
            savepoint.rollback()
        elif rollback_on_failure:
            db.rollback()
        return ProjectPlanApplyResponse(
            status="error",
            message=solver_result.get("message") or "排程失败",
            project_id=project.id,
            schedule_failure=solver_result.get("schedule_failure"),
        )

    schedule_run_id = str(solver_result.get("schedule_run_id") or "")
    new_windows = _task_windows(db, replan_task_ids, schedule_run_id)
    impact_roles = {
        task.id: "inserted" if task.id in selected_task_ids else "shifted"
        for task in replan_tasks
    }
    impacts = _build_impacts(
        db,
        replan_tasks,
        selected_task_ids,
        old_windows,
        new_windows,
        impact_roles,
    )
    new_project_completions = _project_completions(db, moved_project_ids, moved_workloads)
    project_impacts = _build_project_impacts(
        movable_tasks,
        impacts,
        old_project_completions,
        new_project_completions,
        moved_workloads,
    )
    clear_replanned_project_dirty(db, replan_tasks)

    response = ProjectPlanApplyResponse(
        status="applied",
        message=apply_success_message(approval_context, moved=bool(movable_tasks)),
        project_id=project.id,
        schedule_run_id=schedule_run_id,
        timeslots_created=int(solver_result.get("timeslots_created", 0)),
        moved_tasks=sum(1 for impact in impacts if not impact.is_insert_task),
        conflicts_checked=True,
        impacts=impacts,
        project_impacts=project_impacts,
    )
    if commit:
        db.commit()
    elif savepoint and retain_changes:
        savepoint.commit()
    elif savepoint:
        savepoint.rollback()
    else:
        db.flush()
    return response


def _load_project_candidates(db, project_id: int) -> tuple[Project, list[Task]]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ProjectPlanNotFoundError("项目不存在")
    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    parent_ids = {task.parent_id for task in tasks if task.parent_id is not None}
    task_by_id = {task.id: task for task in tasks}
    seed_ids = {
        task.id for task in tasks
        if task.schedule_dirty or task.status in {"pending", "ready"}
    }
    affected_ids = _downstream_ids(db, seed_ids, set(task_by_id))
    candidates = [
        task for task in tasks
        if task.id in affected_ids
        and task.id not in parent_ids
        and not task.is_external_gate
        and task.status != "waiting_external"
        and task.schedule_lock_status == "none"
    ]
    return project, sorted(candidates, key=lambda task: (task.created_at, task.id))


def _load_insert_movable_tasks(
    db,
    project: Project,
    selected_tasks: list[Task],
    approval_context: ApprovalScheduleContext | None = None,
) -> list[Task]:
    selected_ids = {task.id for task in selected_tasks}
    is_detection_priority_insert = project.project_kind == "detection"
    insert_priority = int(project.priority or 3)
    movable = load_resource_closure_movable_tasks(
        db,
        insert_priority,
        selected_tasks,
        include_same_priority=not is_detection_priority_insert,
        minimum_start=approval_context.anchor_at if approval_context else None,
        exclude_tasks_with_unfinished_predecessors=is_detection_priority_insert,
    )
    approval_movable = load_approval_resource_queue_tasks(
        db,
        project,
        selected_tasks,
        approval_context,
        _task_has_protected_slot,
    )
    deadline_movable = [] if is_detection_priority_insert else (
        _load_later_deadline_movable_tasks(
            db, project, selected_tasks,
            minimum_start=approval_context.anchor_at if approval_context else None,
        )
    )
    candidates = _unique_tasks(movable + deadline_movable + approval_movable)
    candidates = expand_movable_downstream_tasks(db, candidates)
    if is_detection_priority_insert:
        candidate_ids = {task.id for task in candidates}
        blocked_ids = _tasks_with_unfinished_predecessors(
            db, candidate_ids, allowed_predecessor_ids=candidate_ids,
        )
        candidates = [task for task in candidates if task.id not in blocked_ids]
    if approval_context:
        candidates = [
            task for task in candidates
            if not _has_approved_gate_predecessor(task)
    ]
    return candidates

def _load_later_deadline_movable_tasks(
    db,
    project: Project,
    selected_tasks: list[Task],
    minimum_start: datetime | None = None,
) -> list[Task]:
    """Return future, unstarted tasks from projects with a later deadline.

    Fully protected tasks are excluded. A task with both a frozen segment and
    later confirmed segments remains movable: the frozen work stays fixed and
    only its remaining work is replanned.
    """
    if not project.end_date:
        return []
    selected_ids = {task.id for task in selected_tasks}
    selected_instruments = _selected_instrument_ids(selected_tasks)
    selected_assignees = {task.assignee_id for task in selected_tasks if task.assignee_id}
    if not selected_instruments and not selected_assignees:
        return []
    candidates = db.query(Task).join(Project).filter(
        Task.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
        ~Task.id.in_(selected_ids),
        Project.id != project.id,
        Project.end_date.isnot(None),
        Project.end_date > project.end_date,
    ).order_by(Project.end_date, Project.priority, Task.created_at, Task.id).all()
    if not candidates:
        return []
    # 资源过滤条件与具体任务无关，原先在循环里重复拼了 N 遍。
    resource_filters = []
    if selected_instruments:
        resource_filters.append(TimeSlot.instrument_id.in_(selected_instruments))
    elif selected_assignees:
        resource_filters.append(Task.assignee_id.in_(selected_assignees))
    candidate_ids = [task.id for task in candidates]
    protected_ids = _fully_protected_task_ids(db, set(candidate_ids))
    conflicting_ids = {
        task_id for (task_id,) in db.query(TimeSlot.task_id).join(Task).filter(
            TimeSlot.task_id.in_([
                task_id for task_id in candidate_ids if task_id not in protected_ids
            ]),
            TimeSlot.tier.in_(MOVABLE_TIERS),
            TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
            TimeSlot.plan_end > (minimum_start or datetime.now()),
            *resource_filters,
        ).distinct().all()
    }
    if not conflicting_ids:
        return []

    project_task_ids = {
        task_id for task_id, in db.query(Task.id).join(Project).filter(
            Project.end_date > project.end_date,
        ).all()
    }
    branches = {
        task_id: _downstream_ids(db, {task_id}, project_task_ids)
        for task_id in conflicting_ids
    }
    # 原先每个分支上的每个任务都单独判一次"是否整个被保护住"，而那个判断本身
    # 又是两条 SQL。这里先把所有涉及的任务凑齐，一次判完再回来筛。
    fully_protected = _fully_protected_task_ids(
        db, {task_id for branch in branches.values() for task_id in branch},
    )
    affected_ids = set()
    for branch_ids in branches.values():
        if any(task_id in fully_protected for task_id in branch_ids):
            continue
        affected_ids.update(branch_ids)
    if not affected_ids:
        return []
    affected_tasks = db.query(Task).filter(
        Task.id.in_(affected_ids),
        Task.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
    ).all()
    return [task for task in affected_tasks if task.id not in fully_protected]


def _fully_protected_task_ids(db, task_ids: set[int]) -> set[int]:
    """一次判出这批任务里哪些"整个被保护住"——有不可移动的槽，且没有任何还能挪的槽。"""
    if not task_ids:
        return set()
    protected = tasks_with_immovable_slot(db, task_ids)
    if not protected:
        return set()
    still_movable = {
        task_id for (task_id,) in db.query(TimeSlot.task_id).filter(
            TimeSlot.task_id.in_(protected),
            TimeSlot.tier.in_(MOVABLE_TIERS),
            TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_end > datetime.now(),
        ).distinct().all()
    }
    return protected - still_movable


def _task_is_fully_protected(db, task_id: int) -> bool:
    return task_id in _fully_protected_task_ids(db, {task_id})


def _task_has_protected_slot(db, task_id: int) -> bool:
    return task_has_immovable_slot(db, task_id)


def _has_approved_gate_predecessor(task: Task) -> bool:
    """Keep formally approved branches stable during forecast insertion."""
    pending = list(task.predecessors)
    visited: set[int] = set()
    while pending:
        dependency = pending.pop()
        predecessor = dependency.predecessor
        if predecessor.id in visited:
            continue
        visited.add(predecessor.id)
        if predecessor.is_external_gate and predecessor.gate_status == "approved":
            return True
        pending.extend(predecessor.predecessors)
    return False


def _selected_tasks_start_today(
    db,
    selected_tasks: list[Task],
    schedule_run_id: str | None,
) -> bool:
    if not schedule_run_id or not selected_tasks:
        return False
    today = datetime.now().date()
    return db.query(TimeSlot.id).filter(
        TimeSlot.task_id.in_([task.id for task in selected_tasks]),
        TimeSlot.schedule_run_id == schedule_run_id,
        TimeSlot.plan_start >= datetime.combine(today, datetime.min.time()),
        TimeSlot.plan_start < datetime.combine(today + timedelta(days=1), datetime.min.time()),
    ).first() is not None


def _released_slot_intervals(db, task_ids: set[int]) -> dict[int, list[tuple]]:
    """即将被删除的时间槽快照：任务 → [(计划开始, 计划结束, 仪器)]。

    筛选条件与 _movable_slots_query 保持一致，两者必须同进同出。
    """
    intervals: dict[int, list[tuple]] = {}
    for slot in _movable_slots_query(db, task_ids).all():
        if not slot.plan_start or not slot.plan_end:
            continue
        intervals.setdefault(slot.task_id, []).append(
            (slot.plan_start, slot.plan_end, slot.instrument_id),
        )
    return intervals


def _movable_slots_query(db, task_ids: set[int]):
    return db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.tier.in_(MOVABLE_TIERS),
        TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
        TimeSlot.actual_start.is_(None),
    )


def _downstream_ids(db, seed_ids: set[int], project_task_ids: set[int]) -> set[int]:
    if not seed_ids:
        return set()
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(project_task_ids),
    ).all()
    downstream_by_predecessor: dict[int, set[int]] = {}
    for dependency in dependencies:
        downstream_by_predecessor.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)
    affected_ids = set(seed_ids)
    pending_ids = list(seed_ids)
    while pending_ids:
        predecessor_id = pending_ids.pop()
        for downstream_id in downstream_by_predecessor.get(predecessor_id, set()):
            if downstream_id not in affected_ids:
                affected_ids.add(downstream_id)
                pending_ids.append(downstream_id)
    return affected_ids


def _unique_tasks(tasks: list[Task]) -> list[Task]:
    return sorted({task.id: task for task in tasks}.values(), key=lambda task: (task.project_id, task.created_at, task.id))
