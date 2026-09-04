from __future__ import annotations

from datetime import datetime

from app.models import (
    Project,
    Task,
    TaskCapabilityRequirement,
    TaskDependency,
    TaskExecutionSegment,
    TaskNightRun,
    TimeSlot,
    InstrumentBridgeReservation,
)
from app.schemas.schemas import ProjectCreate, TaskUpdate
from app.services.project_hours_validation_service import (
    ProjectHoursExceededError,
    project_top_level_task_hours,
    ProjectWindowCapacityError,
    project_window_capacity_deficit,
    validate_project_estimated_hours,
    validate_project_window_capacity,
)
from app.services.project_task_rollup_service import recalculate_project_parent_hours
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.project_date_service import (
    normalize_project_end,
    normalize_project_start,
    validate_project_window,
)
from app.services.task_dependency_service import resolve_dependency_type
from app.services.project_reference_validation_service import (
    ProjectReferenceInvalidError,
    validate_task_references,
)
from app.services.audit_log_service import (
    record_audit_log,
    task_audit_detail,
    task_audit_snapshot,
    task_deleted_audit_detail,
)


SCHEDULE_FIELDS = {
    "task_type",
    "requires_instrument",
    "requires_human",
    "est_duration_hours",
    "switchover_hours",
    "allow_split",
    "allow_transfer",
    "milestone_id",
    "priority_weight",
    "predecessor_ids",
    "instrument_ids",
    "parent_id",
}
PROTECTED_TASK_STATUSES = {"running", "done", "completed"}


class PlanChangeNotFoundError(Exception):
    pass


class PlanChangeInvalidError(Exception):
    pass


def delete_task_plan(
    db,
    task_id: int,
    allow_completed: bool = False,
    actor_name: str | None = None,
) -> None:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise PlanChangeNotFoundError("任务不存在")
    if not allow_completed and _task_tree_has_completed_task(task):
        raise PlanChangeInvalidError("已完成任务不允许删除")
    audit_detail = task_deleted_audit_detail(db, task) if actor_name else None
    released_resources = _released_task_resources(task)
    project_id = task.project_id
    bridge_pairs: set[tuple[int, int]] = set()
    affected_tasks: list[Task] = []
    if task.is_external_gate:
        bridge_pairs, affected_tasks = _prepare_approval_gate_deletion(db, task)

    task_tree = _task_tree(task)
    task_ids = {item.id for item in task_tree}
    for item in task_tree:
        if item in db:
            db.expunge(item)
    db.query(TaskDependency).filter(
        (TaskDependency.predecessor_id.in_(task_ids))
        | (TaskDependency.task_id.in_(task_ids))
    ).delete(synchronize_session=False)
    db.query(TaskCapabilityRequirement).filter(
        TaskCapabilityRequirement.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    db.query(TaskNightRun).filter(
        TaskNightRun.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    db.query(TaskExecutionSegment).filter(
        TaskExecutionSegment.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    slot_query = db.query(TimeSlot).filter(TimeSlot.task_id.in_(task_ids))
    if allow_completed:
        # 管理员明确执行物理删除时，先清理仍引用任务的历史槽，避免
        # time_slot.task_id 外键阻止任务树删除。普通删除仍保留作废槽。
        instrument_ids = {
            instrument_id for instrument_id, in slot_query.with_entities(
                TimeSlot.instrument_id,
            ).distinct().all() if instrument_id
        }
        db.query(InstrumentBridgeReservation).filter(
            (InstrumentBridgeReservation.task_id.in_(task_ids))
            | (InstrumentBridgeReservation.previous_task_id.in_(task_ids))
            | (InstrumentBridgeReservation.following_task_id.in_(task_ids))
        ).delete(synchronize_session=False)
        slot_query.delete(synchronize_session=False)
        from app.services.instrument_status_service import refresh_instrument_statuses
        refresh_instrument_statuses(db, instrument_ids)
    else:
        delete_time_slots_and_refresh(db, slot_query)
    db.query(Task).filter(
        Task.id.in_(task_ids),
        Task.id != task_id,
    ).update({Task.parent_id: None}, synchronize_session=False)
    db.query(Task).filter(Task.id.in_(task_ids)).delete(synchronize_session=False)
    db.flush()
    _restore_bridged_dependencies(db, bridge_pairs)
    for affected_task in affected_tasks:
        if affected_task.status == "waiting_external":
            affected_task.status = "pending"
        affected_task.schedule_dirty = True
    recalculate_project_parent_hours(db, project_id)
    _compact_released_resource_queues(db, released_resources)
    if actor_name and audit_detail:
        record_audit_log(db, actor_name, "task_deleted", "task", task_id, audit_detail)
    db.commit()


def _task_tree(task: Task) -> list[Task]:
    tasks: list[Task] = []
    seen_ids: set[int] = set()
    pending = [task]
    while pending:
        current = pending.pop()
        if current.id in seen_ids:
            continue
        seen_ids.add(current.id)
        tasks.append(current)
        pending.extend(current.children)
    return tasks


def _released_task_resources(task: Task) -> set[tuple[int, int | None]]:
    resources: set[tuple[int, int | None]] = set()
    pending = [task]
    while pending:
        current = pending.pop()
        pending.extend(current.children)
        for slot in current.time_slots:
            if slot.instrument_id is not None:
                resources.add((slot.instrument_id, current.assignee_id))
    return resources


def _compact_released_resource_queues(
    db,
    resources: set[tuple[int, int | None]],
) -> None:
    if not resources:
        return
    from app.services.schedule_completion_service import _forward_shift_instrument_queue

    released_at = datetime.now()
    for instrument_id, assignee_id in sorted(resources, key=lambda item: (item[0], item[1] or 0)):
        _forward_shift_instrument_queue(db, instrument_id, released_at, assignee_id)


def _task_tree_has_completed_task(task: Task) -> bool:
    return task.schedule_lock_status == "completed" or any(
        _task_tree_has_completed_task(child) for child in task.children
    )


def update_project_plan(db, project_id: int, data: ProjectCreate) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise PlanChangeNotFoundError("项目不存在")
    start_date = normalize_project_start(data.start_date)
    end_date = normalize_project_end(data.end_date)
    try:
        validate_project_window(start_date, end_date)
    except ValueError as exc:
        raise PlanChangeInvalidError(str(exc)) from exc
    # 任务合计工时不在这个接口里改，新旧两边取同一个值；预计工时和起止日期
    # 会变，所以缺口要分别按改前和改后各算一次。
    task_hours = project_top_level_task_hours(db, project_id)
    planned_hours = max(float(data.estimated_hours or 0), task_hours)
    previous_hours = max(float(project.estimated_hours or 0), task_hours)
    try:
        validate_project_window_capacity(
            db, start_date, end_date, planned_hours,
            previous_deficit=project_window_capacity_deficit(
                db, project.start_date, project.end_date, previous_hours,
            ),
        )
    except ProjectWindowCapacityError as exc:
        raise PlanChangeInvalidError(str(exc)) from exc

    project_code = data.code.strip()
    duplicate = db.query(Project).filter(
        Project.code == project_code,
        Project.id != project_id,
    ).first()
    if duplicate:
        raise PlanChangeInvalidError(f"项目编号 {project_code} 已存在")

    if data.estimated_hours is not None:
        current_task_hours = project_top_level_task_hours(db, project_id)
        if float(data.estimated_hours) < current_task_hours:
            raise PlanChangeInvalidError(
                f"项目预计工时不能低于当前任务总工时 {current_task_hours:g} 小时"
            )

    schedule_changed = any((
        project.priority != data.priority,
        project.start_date != start_date,
        project.end_date != end_date,
    ))
    if schedule_changed:
        _ensure_project_window_keeps_protected_slots(db, project_id, start_date, end_date)

    project.name = data.name.strip()
    project.code = project_code
    project.client_name = data.client_name
    project.estimated_hours = data.estimated_hours
    project.priority = data.priority
    project.manager_id = data.manager_id
    project.start_date = start_date
    project.end_date = end_date
    if schedule_changed:
        _mark_project_movable_tasks_dirty(db, project_id)

    db.commit()
    db.refresh(project)
    return project


def update_task_plan(
    db,
    task_id: int,
    data: TaskUpdate,
    actor_name: str | None = None,
) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise PlanChangeNotFoundError("任务不存在")
    audit_before = task_audit_snapshot(db, task) if actor_name else None

    changes = data.model_dump(exclude_unset=True)
    predecessor_ids = changes.pop("predecessor_ids", None)
    instrument_ids = changes.get("instrument_ids")
    if "name" in changes and not changes["name"]:
        raise PlanChangeInvalidError("任务名称不能为空")
    if predecessor_ids is not None and task.id in predecessor_ids:
        raise PlanChangeInvalidError("任务不能依赖自身")
    try:
        validate_task_references(
            db,
            task.project_id,
            parent_id=changes.get("parent_id", task.parent_id),
            milestone_id=changes.get("milestone_id", task.milestone_id),
            predecessor_ids=list(
                predecessor_ids if predecessor_ids is not None else task.predecessor_ids or []
            ),
            assignee_id=changes.get("assignee_id", task.assignee_id),
            instrument_ids=list(
                instrument_ids if instrument_ids is not None else task.instrument_ids or []
            ),
        )
    except ProjectReferenceInvalidError as exc:
        raise PlanChangeInvalidError(str(exc))
    schedule_changed = _has_schedule_changes(task, changes, predecessor_ids)
    is_structure_change = "parent_id" in changes and task.parent_id != changes["parent_id"]

    if schedule_changed:
        _ensure_schedule_fields_editable(db, task, is_structure_change)

    for field_name, value in changes.items():
        setattr(task, field_name, value)
    if instrument_ids is not None:
        task.instrument_ids = list(instrument_ids)
    if predecessor_ids is not None:
        _replace_dependencies(db, task.id, predecessor_ids)

    recalculate_project_parent_hours(db, task.project_id)
    try:
        validate_project_estimated_hours(db, task.project_id)
    except ProjectHoursExceededError as exc:
        db.rollback()
        raise PlanChangeInvalidError(str(exc))

    if schedule_changed:
        if is_structure_change:
            _mark_project_movable_tasks_dirty(db, task.project_id)
        else:
            _mark_task_and_downstream_dirty(db, task)

    if actor_name and audit_before is not None:
        db.flush()
        db.expire(task, ["predecessors", "assignee", "parent"])
        record_audit_log(
            db,
            actor_name,
            "task_updated",
            "task",
            task.id,
            task_audit_detail(db, task, "修改任务", audit_before),
        )
    db.commit()
    db.refresh(task)
    return task


def _has_schedule_changes(task: Task, changes: dict, predecessor_ids: list[int] | None) -> bool:
    for field_name in SCHEDULE_FIELDS - {"predecessor_ids"}:
        if field_name not in changes:
            continue
        old_value = getattr(task, field_name)
        new_value = changes[field_name]
        if field_name == "instrument_ids":
            if _normalized_ids(old_value) != _normalized_ids(new_value):
                return True
        elif old_value != new_value:
            return True
    if predecessor_ids is not None:
        return _normalized_ids(task.predecessor_ids) != _normalized_ids(predecessor_ids)
    return False


def _ensure_schedule_fields_editable(db, task: Task, is_structure_change: bool) -> None:
    if task.schedule_lock_status != "none":
        raise PlanChangeInvalidError(
            f"任务【{task.name}】已{_lock_label(task.schedule_lock_status)}，只能修改名称和负责人"
        )
    affected = _project_tasks(db, task.project_id) if is_structure_change else _downstream_tasks(db, task)
    protected = [item for item in affected if item.id != task.id and item.schedule_lock_status != "none"]
    if protected:
        names = "、".join(item.name for item in protected[:3])
        raise PlanChangeInvalidError(f"下游存在固定任务【{names}】，不能修改当前任务的排程字段")


def _prepare_approval_gate_deletion(db, gate: Task) -> tuple[set[tuple[int, int]], list[Task]]:
    affected_tasks = [task for task in _downstream_tasks(db, gate) if task.id != gate.id]
    protected = [task for task in affected_tasks if task.schedule_lock_status != "none"]
    if protected:
        names = "、".join(task.name for task in protected[:3])
        raise PlanChangeInvalidError(f"下游任务【{names}】已冻结、运行或完成，不能删除方案签批")
    predecessor_ids = {
        item.predecessor_id
        for item in db.query(TaskDependency).filter(TaskDependency.task_id == gate.id).all()
    }
    unlock_task_ids = {
        item.task_id
        for item in db.query(TaskDependency).filter(TaskDependency.predecessor_id == gate.id).all()
    }
    return {
        (predecessor_id, unlock_task_id)
        for predecessor_id in predecessor_ids
        for unlock_task_id in unlock_task_ids
        if predecessor_id != unlock_task_id
    }, affected_tasks


def _restore_bridged_dependencies(db, bridge_pairs: set[tuple[int, int]]) -> None:
    for predecessor_id, task_id in sorted(bridge_pairs):
        exists = db.query(TaskDependency).filter(
            TaskDependency.task_id == task_id,
            TaskDependency.predecessor_id == predecessor_id,
        ).first()
        if not exists:
            db.add(TaskDependency(task_id=task_id, predecessor_id=predecessor_id))


def _ensure_project_window_keeps_protected_slots(
    db,
    project_id: int,
    start_date: datetime | None,
    end_date: datetime | None,
) -> None:
    tasks = _project_tasks(db, project_id)
    protected_ids = {task.id for task in tasks if task.schedule_lock_status != "none"}
    if not protected_ids:
        return
    slots = db.query(TimeSlot).filter(TimeSlot.task_id.in_(protected_ids)).all()
    for slot in slots:
        if start_date and slot.plan_start < start_date:
            raise PlanChangeInvalidError(f"项目开始时间晚于固定任务【{slot.task.name}】的排程时间")
        if end_date and slot.plan_end > end_date:
            raise PlanChangeInvalidError(f"项目结束时间早于固定任务【{slot.task.name}】的排程时间")


def _mark_task_and_downstream_dirty(db, task: Task) -> None:
    for affected_task in _downstream_tasks(db, task):
        if affected_task.schedule_lock_status == "none":
            affected_task.schedule_dirty = True


def _mark_project_movable_tasks_dirty(db, project_id: int) -> None:
    for task in _project_tasks(db, project_id):
        if task.schedule_lock_status == "none":
            task.schedule_dirty = True


def _downstream_tasks(db, task: Task) -> list[Task]:
    tasks = _project_tasks(db, task.project_id)
    task_by_id = {item.id: item for item in tasks}
    downstream_by_predecessor: dict[int, set[int]] = {}
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(task_by_id),
    ).all()
    for dependency in dependencies:
        downstream_by_predecessor.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)

    affected_ids = {task.id}
    pending_ids = [task.id]
    while pending_ids:
        predecessor_id = pending_ids.pop()
        for downstream_id in downstream_by_predecessor.get(predecessor_id, set()):
            if downstream_id not in affected_ids:
                affected_ids.add(downstream_id)
                pending_ids.append(downstream_id)
    return [task_by_id[task_id] for task_id in affected_ids if task_id in task_by_id]


def _replace_dependencies(db, task_id: int, predecessor_ids: list[int]) -> None:
    task = db.query(Task).filter(Task.id == task_id).first()
    db.query(TaskDependency).filter(TaskDependency.task_id == task_id).delete()
    for predecessor_id in sorted(set(predecessor_ids)):
        # 按规则重算而不是沿用旧类型：改父分组、改任务类型都会让一条关系从成立变
        # 不成立，反过来也一样。此前只降级不升级，规则新成立的关系永远补不上。
        predecessor = db.query(Task).filter(Task.id == predecessor_id).first()
        db.add(TaskDependency(
            task_id=task_id,
            predecessor_id=predecessor_id,
            dependency_type=resolve_dependency_type(predecessor, task),
        ))


def _project_tasks(db, project_id: int) -> list[Task]:
    return db.query(Task).filter(Task.project_id == project_id).all()


def _normalized_ids(values) -> tuple[int, ...]:
    return tuple(sorted(int(value) for value in (values or [])))


def _lock_label(lock_status: str) -> str:
    return {"frozen": "冻结", "running": "开始运行", "completed": "完成"}.get(lock_status, "锁定")
