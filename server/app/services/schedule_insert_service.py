from __future__ import annotations

from datetime import datetime

from app.models import Project, Task, TaskDependency, TimeSlot
from app.services.schedule_slot_protection_service import task_has_immovable_slot
from app.schemas.schemas import (
    InsertOrderImpact,
    InsertOrderPreview,
    InsertOrderRequest,
    InsertOrderResult,
)
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.schedule_insert_resources import (
    anchor_schedule_end,
    resource_queue_task_ids,
)
from app.services.task_delay_status_service import reset_task_delay
from app.services.schedule_working_time_service import working_hours_between


class ScheduleInsertNotFoundError(Exception):
    pass


class ScheduleInsertInvalidError(Exception):
    pass


def _insert_notification_reason(operator_name: str | None) -> str:
    return (
        f"插单导致（操作人：{operator_name}）"
        if operator_name else "插单导致"
    )


def preview_insert(db, data: InsertOrderRequest) -> InsertOrderPreview:
    preview = _execute_insert(db, data, commit=False)
    db.rollback()
    return preview


def confirm_insert(
    db,
    data: InsertOrderRequest,
    operator_name: str | None = None,
) -> InsertOrderResult:
    preview = _execute_insert(db, data, commit=True, operator_name=operator_name)
    selected_tasks = db.query(Task).filter(Task.id.in_(data.task_ids)).all()
    anchor = db.query(Task).filter(Task.id == data.anchor_task_id).first()
    return InsertOrderResult(
        status="ok",
        schedule_run_id=preview.schedule_run_id,
        timeslots_created=preview.timeslots_created,
        moved_tasks=len(preview.impacts),
        conflicts_checked=True,
        impacts=preview.impacts,
        audit_detail=_insert_audit_detail(
            selected_tasks,
            anchor,
            len(preview.impacts),
            preview.schedule_run_id,
        ),
    )


def _insert_audit_detail(
    selected_tasks: list[Task],
    anchor: Task | None,
    moved_tasks: int,
    schedule_run_id: str,
) -> dict[str, object]:
    inserted = "、".join(_task_display(task) for task in selected_tasks)
    anchor_display = _task_display(anchor) if anchor else "未指定任务"
    return {
        "insert_summary": f"将【{inserted}】插入到【{anchor_display}】之后",
        "moved_tasks": moved_tasks,
        "schedule_run_id": schedule_run_id,
    }


def _task_display(task: Task) -> str:
    project = task.project
    project_display = " · ".join(
        part for part in [project.code, project.name] if part
    ) if project else "未知项目"
    return f"{project_display} · {task.name}"


def _execute_insert(
    db,
    data: InsertOrderRequest,
    commit: bool,
    operator_name: str | None = None,
) -> InsertOrderPreview:
    project = db.query(Project).filter(Project.id == data.project_id).first()
    if not project:
        raise ScheduleInsertNotFoundError("插单项目不存在")

    selected_tasks = db.query(Task).filter(Task.id.in_(data.task_ids)).all()
    if len(selected_tasks) != len(set(data.task_ids)):
        raise ScheduleInsertNotFoundError("插单任务不存在")
    if any(task.project_id != project.id for task in selected_tasks):
        raise ScheduleInsertInvalidError("插单任务必须属于所选项目")

    selected_task_ids = {task.id for task in selected_tasks}
    _ensure_selected_tasks_are_movable(db, selected_task_ids)
    _ensure_leaf_tasks(selected_tasks, "插单任务")
    context = _build_insert_context(db, data, selected_tasks)
    replan_tasks = context["replan_tasks"]
    replan_task_ids = {task.id for task in replan_tasks}
    old_windows = _task_windows(db, replan_task_ids)

    delete_time_slots_and_refresh(db, db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(replan_task_ids),
        TimeSlot.tier.in_(["frozen", "confirmed", "forecast"]),
        TimeSlot.status.in_(["scheduled", "blocked"]),
    ), synchronize_session=False)
    for task in replan_tasks:
        task.status = "pending"
        reset_task_delay(task)
    db.flush()

    from app.services.scheduler import SchedulerService

    result = SchedulerService(db).generate(
        task_ids=sorted(replan_task_ids),
        mode="insert",
        commit=False,
        original_schedule_windows=old_windows,
        stability_task_ids=context["stability_task_ids"],
        additional_dependencies=context["dependency_pairs"],
        advance_notification_reason=_insert_notification_reason(operator_name),
        emit_advance_notifications=commit,
        current_project_id=project.id,
    )
    if result.get("status") != "ok":
        db.rollback()
        raise ScheduleInsertInvalidError(result.get("message") or "插单排程失败")

    schedule_run_id = result.get("schedule_run_id", "")
    new_windows = _task_windows(db, replan_task_ids, schedule_run_id)
    impacts = _build_impacts(
        db,
        replan_tasks,
        selected_task_ids,
        old_windows,
        new_windows,
        context["impact_roles"],
    )
    preview = InsertOrderPreview(
        status="ok",
        schedule_run_id=schedule_run_id,
        timeslots_created=int(result.get("timeslots_created", 0)),
        total_delay_hours=round(sum(max(0, impact.delay_hours) for impact in impacts), 1),
        impacts=impacts,
    )
    if commit:
        db.commit()
    return preview


def _build_insert_context(db, data: InsertOrderRequest, selected_tasks: list[Task]) -> dict:
    if data.mode == "priority":
        insert_priority = data.priority_override if data.priority_override is not None else int(selected_tasks[0].project.priority or 999)
        movable_tasks = _load_lower_priority_movable_tasks(
            db,
            insert_priority,
            {task.id for task in selected_tasks},
            _selected_instrument_ids(selected_tasks),
        )
        replan_tasks = _unique_tasks(selected_tasks + movable_tasks)
        return {
            "replan_tasks": replan_tasks,
            "dependency_pairs": [],
            "stability_task_ids": set(),
            "impact_roles": {
                task.id: "inserted" if task.id in {item.id for item in selected_tasks} else "shifted"
                for task in replan_tasks
            },
        }
    return _build_custom_insert_context(db, data.anchor_task_id, selected_tasks)


def _build_custom_insert_context(db, anchor_task_id: int | None, selected_tasks: list[Task]) -> dict:
    if anchor_task_id is None:
        raise ScheduleInsertInvalidError("请选择插入位置任务")
    anchor = db.query(Task).filter(Task.id == anchor_task_id).first()
    if anchor is None:
        raise ScheduleInsertNotFoundError("插入位置任务不存在")
    _ensure_leaf_tasks([anchor], "插入位置任务")
    selected_ids = {task.id for task in selected_tasks}
    if anchor.id in selected_ids:
        raise ScheduleInsertInvalidError("插入位置任务不能与插单任务相同")
    if any(not _task_has_schedule(db, task.id) for task in selected_tasks):
        raise ScheduleInsertInvalidError("指定位置插单只能选择已经生成排程的任务")
    if not _task_has_schedule(db, anchor.id):
        raise ScheduleInsertInvalidError("插入位置任务尚未排程，不能作为插入锚点")

    all_anchor_downstream_ids = _downstream_task_ids(db, {anchor.id}) - {anchor.id}
    _ensure_movable_downstream(db, all_anchor_downstream_ids, "插入位置任务的后续任务")
    anchor_downstream_ids = _movable_scheduled_task_ids(db, all_anchor_downstream_ids)
    all_source_downstream_ids = _downstream_task_ids(db, selected_ids) - selected_ids
    _ensure_movable_downstream(db, all_source_downstream_ids, "插单任务的后续任务")
    source_downstream_ids = _movable_scheduled_task_ids(db, all_source_downstream_ids)
    source_roots, source_terminals = _selected_boundaries(db, selected_ids)
    anchor_end = anchor_schedule_end(db, anchor.id)
    resource_queue_ids = resource_queue_task_ids(
        db,
        selected_tasks,
        anchor_end,
        selected_ids | anchor_downstream_ids | source_downstream_ids,
    ) if anchor_end else set()
    all_resource_downstream_ids = _downstream_task_ids(db, resource_queue_ids)
    _ensure_movable_downstream(db, all_resource_downstream_ids, "资源队列后续任务")
    resource_downstream_ids = _movable_scheduled_task_ids(
        db,
        all_resource_downstream_ids,
    )

    direct_anchor_successors = {
        task_id for task_id, in db.query(TaskDependency.task_id).filter(
            TaskDependency.predecessor_id == anchor.id,
        ).all()
        if task_id not in selected_ids
    }
    dependency_pairs = [
        (task_id, anchor.id)
        for task_id in source_roots
    ] + [
        (task_id, predecessor_id)
        for task_id in direct_anchor_successors
        for predecessor_id in source_terminals
    ] + [
        (task_id, predecessor_id)
        for task_id in resource_downstream_ids
        for predecessor_id in source_terminals
    ]
    _ensure_dependency_pairs_valid(db, dependency_pairs)
    replan_ids = (
        selected_ids
        | anchor_downstream_ids
        | source_downstream_ids
        | resource_downstream_ids
    )
    replan_tasks = db.query(Task).filter(
        Task.id.in_(replan_ids),
        Task.is_external_gate.is_(False),
    ).all()
    impact_roles = {
        task.id: (
            "inserted" if task.id in selected_ids
            else "anchor_downstream" if task.id in anchor_downstream_ids
            else "source_downstream"
            if task.id in source_downstream_ids
            else "shifted"
        )
        for task in replan_tasks
    }
    return {
        "replan_tasks": _unique_tasks(replan_tasks),
        "dependency_pairs": dependency_pairs,
        "impact_roles": impact_roles,
        "stability_task_ids": source_downstream_ids | resource_downstream_ids,
    }


def _ensure_leaf_tasks(tasks: list[Task], label: str) -> None:
    if any(task.children for task in tasks):
        raise ScheduleInsertInvalidError(f"{label}必须选择叶子任务")
    if any(task.is_external_gate for task in tasks):
        raise ScheduleInsertInvalidError(f"{label}不能选择方案签批节点")


def _task_has_schedule(db, task_id: int) -> bool:
    return db.query(TimeSlot.id).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.status.in_(["scheduled", "running", "completed", "paused", "blocked", "interrupted"]),
    ).first() is not None


def _movable_scheduled_task_ids(db, task_ids: set[int]) -> set[int]:
    if not task_ids:
        return set()
    protected_task_ids = {
        task_id for task_id, in db.query(TimeSlot.task_id).filter(
            TimeSlot.task_id.in_(task_ids),
            (
                (TimeSlot.tier == "frozen")
                | TimeSlot.status.in_(["running", "completed"])
                | TimeSlot.actual_start.isnot(None)
            ),
        ).distinct().all()
    }
    return {
        task_id for task_id, in db.query(TimeSlot.task_id).filter(
            TimeSlot.task_id.in_(task_ids),
            ~TimeSlot.task_id.in_(protected_task_ids) if protected_task_ids else True,
            TimeSlot.tier.in_(["confirmed", "forecast"]),
            TimeSlot.status.in_(["scheduled", "blocked"]),
        ).distinct().all()
    }


def _selected_boundaries(db, selected_ids: set[int]) -> tuple[set[int], set[int]]:
    dependencies = db.query(TaskDependency).filter(
        (TaskDependency.task_id.in_(selected_ids)) | (TaskDependency.predecessor_id.in_(selected_ids)),
    ).all()
    roots = set(selected_ids)
    terminals = set(selected_ids)
    for dependency in dependencies:
        if dependency.task_id in selected_ids and dependency.predecessor_id in selected_ids:
            roots.discard(dependency.task_id)
            terminals.discard(dependency.predecessor_id)
    return roots, terminals


def _downstream_task_ids(db, seed_ids: set[int]) -> set[int]:
    downstream_by_predecessor: dict[int, set[int]] = {}
    for dependency in db.query(TaskDependency).all():
        downstream_by_predecessor.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)
    affected = set(seed_ids)
    pending = list(seed_ids)
    while pending:
        predecessor_id = pending.pop()
        for task_id in downstream_by_predecessor.get(predecessor_id, set()):
            if task_id not in affected:
                affected.add(task_id)
                pending.append(task_id)
    return affected


def _ensure_movable_downstream(db, task_ids: set[int], label: str) -> None:
    if not task_ids:
        return
    protected_task = db.query(Task.id).filter(
        Task.id.in_(task_ids),
        Task.status.in_(["running", "completed", "done"]),
    ).first()
    protected = db.query(TimeSlot.id).filter(
        TimeSlot.task_id.in_(task_ids),
        (
            TimeSlot.status.in_(["running", "completed"])
            | TimeSlot.actual_start.isnot(None)
        ),
    ).first()
    if protected_task or protected:
        raise ScheduleInsertInvalidError(f"{label}中包含已开始或已完成任务，不能插入")


def _ensure_dependency_pairs_valid(db, pairs: list[tuple[int, int]]) -> None:
    existing_pairs = {
        (dependency.task_id, dependency.predecessor_id)
        for dependency in db.query(TaskDependency).all()
    }
    # A repeated preview may submit dependencies that the first confirmed
    # insert already persisted. They are idempotent, not cycles.
    new_pairs = {pair for pair in pairs if pair not in existing_pairs}
    graph: dict[int, set[int]] = {}
    for task_id, predecessor_id in existing_pairs | new_pairs:
        graph.setdefault(predecessor_id, set()).add(task_id)
    for task_id, predecessor_id in new_pairs:
        if task_id == predecessor_id or _has_path(graph, task_id, predecessor_id):
            raise ScheduleInsertInvalidError("自定义插入会形成循环前置关系")


def _has_path(graph: dict[int, set[int]], start_id: int, target_id: int) -> bool:
    pending = [start_id]
    visited = set()
    while pending:
        current = pending.pop()
        if current == target_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, set()))
    return False


def _unique_tasks(tasks: list[Task]) -> list[Task]:
    return list({task.id: task for task in tasks}.values())


def _ensure_selected_tasks_are_movable(db, task_ids: set[int]) -> None:
    protected_slot = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        (
            TimeSlot.status.in_(["running", "completed"])
            | TimeSlot.actual_start.isnot(None)
        ),
    ).first()
    if protected_slot:
        raise ScheduleInsertInvalidError("运行中、已开始或已完成的任务不能作为插单任务重新排程")


def _load_lower_priority_movable_tasks(
    db,
    insert_priority: int,
    excluded_task_ids: set[int],
    selected_instrument_ids: set[int],
    selected_assignee_ids: set[int] | None = None,
    include_same_priority: bool = False,
    unstarted_projects_only: bool = False,
    minimum_start: datetime | None = None,
) -> list[Task]:
    selected_assignee_ids = selected_assignee_ids or set()
    priority_filter = (
        Project.priority >= insert_priority
        if include_same_priority
        else Project.priority > insert_priority
    )
    candidate_tasks = db.query(Task).join(Project).filter(
        priority_filter,
        Task.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
        ~Task.id.in_(excluded_task_ids),
    ).order_by(Project.priority, Task.created_at, Task.id).all()
    movable = []
    for task in candidate_tasks:
        if unstarted_projects_only and _project_has_started(db, task.project_id):
            continue
        has_protected_slot = task_has_immovable_slot(db, task.id)
        resource_filters = []
        if selected_instrument_ids:
            resource_filters.append(TimeSlot.instrument_id.in_(selected_instrument_ids))
        if selected_assignee_ids:
            resource_filters.append(
                Task.requires_human.is_(True) & Task.assignee_id.in_(selected_assignee_ids)
            )
        if not resource_filters:
            continue
        has_future_slot = db.query(TimeSlot.id).join(Task).filter(
            TimeSlot.task_id == task.id,
            TimeSlot.lifecycle_status == "active",
            TimeSlot.tier.in_(["confirmed", "forecast"]),
            TimeSlot.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
            TimeSlot.plan_end > (minimum_start or datetime.now()),
            (resource_filters[0] if len(resource_filters) == 1 else resource_filters[0] | resource_filters[1]),
        ).first()
        if not has_protected_slot and has_future_slot:
            movable.append(task)
    return movable


def _project_has_started(db, project_id: int) -> bool:
    started_task = db.query(Task.id).filter(
        Task.project_id == project_id,
        Task.status.in_(["running", "completed", "done"]),
    ).first()
    if started_task:
        return True
    started_slot = db.query(TimeSlot.id).join(Task).filter(
        Task.project_id == project_id,
        (
            TimeSlot.actual_start.isnot(None)
            | TimeSlot.status.in_(["running", "completed"])
        ),
    ).first()
    return started_slot is not None


def _selected_instrument_ids(tasks: list[Task]) -> set[int]:
    instrument_ids = set()
    for task in tasks:
        raw_ids = task.instrument_ids or []
        if isinstance(raw_ids, list):
            instrument_ids.update(int(instrument_id) for instrument_id in raw_ids)
    return instrument_ids


def _task_windows(db, task_ids: set[int], schedule_run_id: str | None = None) -> dict[int, tuple[datetime, datetime]]:
    """各任务现行计划的起止区间，供排程影响对比使用。

    必须排除作废的槽。把历次被推翻的版本一起取最早开始、最晚结束，得到的是所有
    版本的并集——头取自一个版本、尾取自另一个版本，这个区间从未存在过。排程影响
    弹窗据此显示「原计划」，于是一个 2.5 小时、位置从未变过的任务，会被显示成
    横跨五天然后"缩回"原位，还被误列进被顺延任务。
    """
    query = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
    )
    if schedule_run_id:
        query = query.filter(TimeSlot.schedule_run_id == schedule_run_id)
    windows: dict[int, tuple[datetime, datetime]] = {}
    for slot in query.order_by(TimeSlot.plan_start, TimeSlot.id).all():
        if slot.task_id not in windows:
            windows[slot.task_id] = (slot.plan_start, slot.plan_end)
            continue
        start, end = windows[slot.task_id]
        windows[slot.task_id] = (min(start, slot.plan_start), max(end, slot.plan_end))
    return windows


def _build_impacts(
    db,
    tasks: list[Task],
    selected_task_ids: set[int],
    old_windows: dict[int, tuple[datetime, datetime]],
    new_windows: dict[int, tuple[datetime, datetime]],
    impact_roles: dict[int, str],
) -> list[InsertOrderImpact]:
    impacts = []
    for task in tasks:
        new_window = new_windows.get(task.id)
        if not new_window:
            continue
        old_window = old_windows.get(task.id)
        if old_window == new_window:
            continue
        delay_hours = 0.0
        if old_window:
            delay_hours = working_hours_between(
                db,
                old_window[1],
                new_window[1],
                _task_impact_instrument_id(db, task.id),
            )
        impacts.append(InsertOrderImpact(
            task_id=task.id,
            task_name=task.name,
            project_id=task.project_id,
            project_name=task.project.name if task.project else "",
            is_insert_task=task.id in selected_task_ids,
            impact_role=impact_roles.get(task.id),
            original_start=old_window[0] if old_window else None,
            original_end=old_window[1] if old_window else None,
            new_start=new_window[0],
            new_end=new_window[1],
            delay_hours=round(delay_hours, 1),
        ))
    return impacts


def _task_impact_instrument_id(db, task_id: int) -> int | None:
    slot = (
        db.query(TimeSlot.instrument_id)
        .filter(
            TimeSlot.task_id == task_id,
            TimeSlot.instrument_id.isnot(None),
        )
        .order_by(TimeSlot.plan_start.desc(), TimeSlot.id.desc())
        .first()
    )
    return slot[0] if slot else None
