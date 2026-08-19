from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Project, Task, TaskDependency, TimeSlot
from app.schemas.schemas import (
    InsertOrderImpact,
    ProjectPlanApplyResponse,
    ProjectPlanInsertConfirmRequest,
    ProjectScheduleImpact,
)
from app.services.project_hours_validation_service import (
    ProjectHoursExceededError,
    validate_project_estimated_hours,
)
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.approval_gate_schedule_context import ApprovalScheduleContext
from app.services.project_plan_apply_helpers import (
    approval_earliest_bounds,
    apply_success_message,
    load_approval_resource_queue_tasks,
    plan_fingerprint,
)
from app.services.task_delay_status_service import reset_task_delay
from app.services.project_instrument_validation_service import (
    RequiredInstrumentError,
    validate_required_task_instruments,
)
from app.services.project_task_rollup_service import recalculate_project_parent_hours
from app.services.schedule_insert_service import (
    _build_impacts,
    _load_lower_priority_movable_tasks,
    _selected_instrument_ids,
    _task_windows,
)


MOVABLE_TIERS = ["confirmed", "forecast"]
MOVABLE_SLOT_STATUSES = ["scheduled", "blocked"]


class ProjectPlanNotFoundError(Exception):
    pass


class ProjectPlanInvalidError(Exception):
    pass


def apply_project_plan(
    db,
    project_id: int,
    approval_context: ApprovalScheduleContext | None = None,
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

    stable_result = _execute_replan(
        db, project, selected_tasks, [], commit=False,
        approval_context=approval_context,
    )
    if stable_result.status == "applied":
        db.rollback()
        # 普通项目也需要参与资源队列重排：同优先级下，截止日期更早的
        # 新项目应能将尚未开始且未冻结的晚截止项目顺延。可移动任务
        # 由 _load_insert_movable_tasks 统一过滤，运行中/已开始/已冻结
        # 的任务不会被纳入。
        movable_tasks = (
            _load_insert_movable_tasks(db, project, selected_tasks, approval_context)
            if project.project_kind == "detection" or hasattr(project, "priority")
            else []
        )
        should_insert = bool(movable_tasks)
        if should_insert:
            if project.project_kind == "detection" and approval_context is None:
                return _execute_replan(
                    db, project, selected_tasks, movable_tasks, commit=True,
                )
            try:
                return _preview_plan_insert(
                    db,
                    project,
                    selected_tasks,
                    stable_result.message,
                    approval_context,
                )
            finally:
                db.rollback()
        return _execute_replan(
            db, project, selected_tasks, [], commit=True,
            approval_context=approval_context,
        )
    db.rollback()

    if project.project_kind == "detection" and approval_context is None:
        movable_tasks = _load_insert_movable_tasks(
            db, project, selected_tasks, approval_context,
        )
        if movable_tasks:
            return _execute_replan(
                db, project, selected_tasks, movable_tasks, commit=True,
            )

    try:
        return _preview_plan_insert(
            db,
            project,
            selected_tasks,
            stable_result.message,
            approval_context,
        )
    finally:
        db.rollback()


def confirm_project_plan_insert(
    db,
    data: ProjectPlanInsertConfirmRequest,
    approval_context: ApprovalScheduleContext | None = None,
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
    preview_token = plan_fingerprint(
        db, project, selected_tasks + movable_tasks, approval_context,
    )
    if preview_token != data.preview_token:
        raise ProjectPlanInvalidError("计划或排程数据已变化，请重新计算影响")
    result = _execute_replan(
        db, project, selected_tasks, movable_tasks, commit=True,
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
    stable_message: str | None,
    approval_context: ApprovalScheduleContext | None = None,
) -> ProjectPlanApplyResponse:
    movable_tasks = _load_insert_movable_tasks(
        db, project, selected_tasks, approval_context,
    )
    if not movable_tasks:
        return ProjectPlanApplyResponse(
            status="error",
            message=stable_message or "没有可移动的低优先级任务，无法完成重排",
            project_id=project.id,
        )
    preview_token = plan_fingerprint(
        db, project, selected_tasks + movable_tasks, approval_context,
    )
    preview = _execute_replan(
        db, project, selected_tasks, movable_tasks, commit=False,
        approval_context=approval_context,
    )
    if preview.status != "applied":
        return ProjectPlanApplyResponse(
            status="error",
            message=preview.message or stable_message or "插单模拟失败",
            project_id=project.id,
        )
    if preview.moved_tasks == 0:
        preview.message = (
            apply_success_message(approval_context, moved=False)
            if approval_context else "排程完成，未顺延其他任务"
        )
        db.commit()
        return preview
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
) -> ProjectPlanApplyResponse:
    replan_tasks = _unique_tasks(selected_tasks + movable_tasks)
    replan_task_ids = {task.id for task in replan_tasks}
    selected_task_ids = {task.id for task in selected_tasks}
    old_windows = _task_windows(db, replan_task_ids)
    moved_project_ids = {task.project_id for task in movable_tasks}
    old_project_completions = _project_completions(db, moved_project_ids)

    _delete_movable_slots(db, replan_task_ids)
    for task in replan_tasks:
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
            _approval_insert_dependencies(selected_tasks, movable_tasks)
            if approval_context
            else _priority_insert_dependencies(project, selected_tasks, movable_tasks)
        ),
        relaxed_project_end_task_ids={
            task.id for task in movable_tasks
        } if approval_context else None,
        earliest_start_bounds=approval_earliest_bounds(approval_context),
        advance_notification_reason="项目任务保存重排",
        emit_advance_notifications=commit,
        early_start_task_ids=(
            approval_context.downstream_task_ids if approval_context else None
        ),
    )
    if solver_result.get("status") != "ok":
        db.rollback()
        return ProjectPlanApplyResponse(
            status="error",
            message=solver_result.get("message") or "排程失败",
            project_id=project.id,
        )

    schedule_run_id = str(solver_result.get("schedule_run_id") or "")
    new_windows = _task_windows(db, replan_task_ids, schedule_run_id)
    impact_roles = {
        task.id: "inserted" if task.id in selected_task_ids else "shifted"
        for task in replan_tasks
    }
    impacts = _build_impacts(
        replan_tasks,
        selected_task_ids,
        old_windows,
        new_windows,
        impact_roles,
    )
    new_project_completions = _project_completions(db, moved_project_ids)
    project_impacts = _build_project_impacts(
        movable_tasks,
        impacts,
        old_project_completions,
        new_project_completions,
    )
    for task in db.query(Task).filter(Task.project_id == project.id).all():
        task.schedule_dirty = False

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
    else:
        db.flush()
    return response


def _approval_insert_dependencies(
    selected_tasks: list[Task],
    movable_tasks: list[Task],
) -> list[tuple[int, int]]:
    # Shared instruments are modeled by capacity and setup constraints in the
    # scheduler. They are not business dependencies and must not impose a
    # cross-project task graph order.
    return []


def _priority_insert_dependencies(
    project: Project,
    selected_tasks: list[Task],
    movable_tasks: list[Task],
) -> list[tuple[int, int]]:
    if project.project_kind != "detection":
        return []
    dependencies = []
    for movable in movable_tasks:
        if int(movable.project.priority or 3) <= int(project.priority or 3):
            continue
        movable_instruments = set(movable.instrument_ids or [])
        for selected in selected_tasks:
            shares_instrument = bool(
                movable_instruments & set(selected.instrument_ids or [])
            )
            shares_assignee = bool(
                movable.assignee_id
                and movable.assignee_id == selected.assignee_id
            )
            if shares_instrument or shares_assignee:
                dependencies.append((movable.id, selected.id))
    return dependencies


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
    movable = _load_lower_priority_movable_tasks(
        db,
        int(project.priority or 3),
        selected_ids,
        _selected_instrument_ids(selected_tasks),
        include_same_priority=not is_detection_priority_insert,
        # 签批插入允许移动“未开始的任务”，即使其所属项目已有其他
        # 任务开始；项目整体已启动不代表该排程任务已经开始。
        unstarted_projects_only=(
            approval_context is None and not is_detection_priority_insert
        ),
        minimum_start=approval_context.anchor_at if approval_context else None,
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

    Protected slots are deliberately excluded so an already started project is
    never displaced by saving a newly scheduled project plan.
    """
    if not project.end_date:
        return []
    selected_ids = {task.id for task in selected_tasks}
    selected_instruments = _selected_instrument_ids(selected_tasks)
    selected_assignees = {task.assignee_id for task in selected_tasks if task.assignee_id}
    if not selected_instruments and not selected_assignees:
        return []
    candidates = db.query(Task).join(Project).filter(
        Task.status == "scheduled",
        ~Task.id.in_(selected_ids),
        Project.id != project.id,
        Project.end_date.isnot(None),
        Project.end_date > project.end_date,
    ).order_by(Project.end_date, Project.priority, Task.created_at, Task.id).all()
    conflicting_ids = set()
    for task in candidates:
        if _task_has_protected_slot(db, task.id):
            continue
        resource_filters = []
        if selected_instruments:
            resource_filters.append(TimeSlot.instrument_id.in_(selected_instruments))
        elif selected_assignees:
            resource_filters.append(Task.assignee_id.in_(selected_assignees))
        future_slot = db.query(TimeSlot.id).join(Task).filter(
            TimeSlot.task_id == task.id,
            TimeSlot.tier.in_(MOVABLE_TIERS),
            TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
            TimeSlot.plan_end > (minimum_start or datetime.now()),
            *resource_filters,
        ).first()
        if future_slot:
            conflicting_ids.add(task.id)
    if not conflicting_ids:
        return []

    project_task_ids = {
        task_id for task_id, in db.query(Task.id).join(Project).filter(
            Project.end_date > project.end_date,
        ).all()
    }
    affected_ids = set()
    for task_id in conflicting_ids:
        branch_ids = _downstream_ids(db, {task_id}, project_task_ids)
        if any(_task_has_protected_slot(db, branch_id) for branch_id in branch_ids):
            continue
        affected_ids.update(branch_ids)
    if not affected_ids:
        return []
    affected_tasks = db.query(Task).filter(
        Task.id.in_(affected_ids),
        Task.status == "scheduled",
    ).all()
    return [
        task for task in affected_tasks
        if not _task_has_protected_slot(db, task.id)
    ]


def _task_has_protected_slot(db, task_id: int) -> bool:
    now = datetime.now()
    return db.query(TimeSlot.id).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.plan_end >= now,
        (
            (TimeSlot.tier == "frozen")
            | TimeSlot.status.in_(["running", "completed"])
            | TimeSlot.actual_start.isnot(None)
        ),
    ).first() is not None


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


def _delete_movable_slots(db, task_ids: set[int]) -> None:
    delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.tier.in_(MOVABLE_TIERS),
        TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
        TimeSlot.actual_start.is_(None),
    ), synchronize_session=False)


def _project_completions(db, project_ids: set[int]) -> dict[int, datetime]:
    if not project_ids:
        return {}
    slots = db.query(TimeSlot).join(Task).filter(
        Task.project_id.in_(project_ids),
        TimeSlot.status.in_([
            "scheduled", "running", "paused", "blocked", "interrupted", "completed",
        ]),
    ).all()
    completions: dict[int, datetime] = {}
    for slot in slots:
        project_id = slot.task.project_id
        current = completions.get(project_id)
        if current is None or slot.plan_end > current:
            completions[project_id] = slot.plan_end
    return completions


def _build_project_impacts(
    movable_tasks: list[Task],
    task_impacts: list[InsertOrderImpact],
    old_completions: dict[int, datetime],
    new_completions: dict[int, datetime],
) -> list[ProjectScheduleImpact]:
    projects = {
        task.project_id: task.project
        for task in movable_tasks
        if task.project is not None
    }
    impacts_by_project: dict[int, list[InsertOrderImpact]] = {}
    for impact in task_impacts:
        if impact.is_insert_task:
            continue
        impacts_by_project.setdefault(impact.project_id, []).append(impact)
    impacts = []
    for project_id, project in sorted(projects.items()):
        original_completion = old_completions.get(project_id)
        new_completion = new_completions.get(project_id)
        project_task_impacts = impacts_by_project.get(project_id, [])
        original_start = min(
            (impact.original_start for impact in project_task_impacts if impact.original_start),
            default=None,
        )
        new_start = min(
            (impact.new_start for impact in project_task_impacts),
            default=None,
        )
        # Report the actual shift of the moved tasks.  Project completion can
        # remain unchanged when another, later task already determines the
        # project's final end time, which previously produced a misleading
        # "顺延 0 小时" message.
        delay_hours = (
            max([0.0, *(impact.delay_hours for impact in project_task_impacts)])
            if project_task_impacts
            else _hours_between(original_completion, new_completion)
        )
        overdue_hours = _hours_between(project.end_date, new_completion)
        impacts.append(ProjectScheduleImpact(
            project_id=project_id,
            project_code=project.code,
            project_name=project.name,
            project_end_date=project.end_date,
            original_start=original_start,
            new_start=new_start,
            original_completion=original_completion,
            new_completion=new_completion,
            delay_hours=round(delay_hours, 1),
            exceeds_end_date=overdue_hours > 0,
            overdue_hours=round(max(0, overdue_hours), 1),
        ))
    return impacts


def _hours_between(start: datetime | None, end: datetime | None) -> float:
    if start is None or end is None:
        return 0
    return (end - start).total_seconds() / 3600


def _project_impact_message(impacts: list[ProjectScheduleImpact]) -> str:
    if not impacts:
        return "需要移动同优先级或低优先级的未开始项目任务，请确认排程影响"
    details = []
    for impact in impacts:
        start_time = (
            impact.new_start.strftime("%Y-%m-%d %H:%M")
            if impact.new_start
            else "暂无"
        )
        delay = max(0, impact.delay_hours)
        deadline = (
            f"超过结题日期 {impact.overdue_hours:g} 小时"
            if impact.exceeds_end_date
            else "未超过结题日期"
        )
        details.append(
            f"项目【{impact.project_code} {impact.project_name}】"
            f"预计顺延 {delay:g} 小时，调整后开始时间为 {start_time}，{deadline}"
        )
    return "需要移动同优先级或低优先级的未开始项目任务，请确认影响：" + "；".join(details)


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
