from __future__ import annotations

from app.models import AuditLog, Instrument, Project, Task, TaskDependency, TaskTypeConfig, User
from app.schemas.project_plan_draft_schemas import (
    ProjectPlanDraftCommitIn,
    ProjectPlanDraftCommitOut,
    ProjectPlanDraftIdMap,
)
from app.services.project_access_service import FULL_PROJECT_ACCESS_ROLES
from app.services.project_hours_validation_service import ProjectHoursExceededError, validate_project_estimated_hours
from app.services.project_instrument_validation_service import RequiredInstrumentError, validate_required_task_instruments
from app.services.project_task_rollup_service import recalculate_project_parent_hours
from app.services.user_role_service import has_any_role


class ProjectPlanDraftNotFoundError(Exception):
    pass


class ProjectPlanDraftPermissionError(Exception):
    pass


class ProjectPlanDraftInvalidError(Exception):
    pass


def commit_project_plan_drafts(
    db,
    project_id: int,
    data: ProjectPlanDraftCommitIn,
    user: User,
) -> ProjectPlanDraftCommitOut:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ProjectPlanDraftNotFoundError("项目不存在")
    if not has_any_role(user, FULL_PROJECT_ACCESS_ROLES) and project.manager_id != user.id:
        raise ProjectPlanDraftPermissionError("无权保存该项目计划")
    client_ids = [item.client_id for item in data.tasks]
    if len(client_ids) != len(set(client_ids)):
        raise ProjectPlanDraftInvalidError("草稿任务标识重复")
    _validate_task_types(db, data)
    _validate_references(db, project_id, data)
    _validate_acyclic_references(db, project_id, data)
    try:
        validate_required_task_instruments(data.tasks)
    except RequiredInstrumentError as exc:
        raise ProjectPlanDraftInvalidError(str(exc))

    id_map: dict[int, int] = {}
    created_by_client_id: dict[int, Task] = {}
    parent_client_ids = {
        item.parent_id for item in data.tasks if item.parent_id is not None
    }
    for item in data.tasks:
        is_parent = item.client_id in parent_client_ids and not item.is_external_gate
        task = Task(
            project_id=project_id,
            name=item.name.strip(),
            task_type=(
                "approval_gate" if item.is_external_gate
                else "group" if is_parent
                else item.task_type
            ),
            requires_instrument=(
                False if item.is_external_gate or is_parent
                else item.requires_instrument
            ),
            requires_human=(
                False if item.is_external_gate or is_parent
                else item.requires_human
            ),
            est_duration_hours=(
                None if item.is_external_gate or is_parent
                else item.estimated_hours
            ),
            switchover_hours=(
                0 if item.is_external_gate or is_parent
                else item.switchover_hours
            ),
            assignee_id=(
                project.manager_id if item.is_external_gate
                else None if is_parent
                else item.assignee_id
            ),
            parent_id=None,
            plan_order=item.plan_order,
            instrument_ids=(
                [] if item.is_external_gate or is_parent
                else item.instrument_ids
            ),
            is_external_gate=item.is_external_gate,
            gate_status="not_submitted" if item.is_external_gate else None,
            status="waiting_external" if item.is_external_gate else "pending",
            schedule_dirty=not item.is_external_gate and not is_parent,
        )
        db.add(task)
        db.flush()
        id_map[item.client_id] = task.id
        created_by_client_id[item.client_id] = task

    for item in data.tasks:
        task = created_by_client_id[item.client_id]
        task.parent_id = _resolve_id(item.parent_id, id_map)
        for predecessor_id in sorted(set(item.predecessor_ids)):
            db.add(TaskDependency(
                task_id=task.id,
                predecessor_id=_resolve_id(predecessor_id, id_map),
            ))
    db.flush()
    for item in data.tasks:
        if not item.is_external_gate:
            continue
        gate_id = id_map[item.client_id]
        for task in _downstream_tasks(db, gate_id):
            if task.schedule_lock_status == "none":
                task.status = "waiting_external"
                task.schedule_dirty = False

    try:
        recalculate_project_parent_hours(db, project_id)
        validate_project_estimated_hours(db, project_id)
    except ProjectHoursExceededError as exc:
        db.rollback()
        raise ProjectPlanDraftInvalidError(str(exc))
    db.add(AuditLog(
        user_name=user.display_name or user.username,
        action="project_plan_drafts_committed",
        target_type="project",
        target_id=project_id,
        detail={
            "target_display": f"{project.code} · {project.name}",
            "created": len(data.tasks),
            "task_details": _plan_task_audit_details(db, data, id_map),
        },
    ))
    db.commit()
    return ProjectPlanDraftCommitOut(
        status="ok",
        message=f"已保存 {len(data.tasks)} 个计划节点",
        created=len(data.tasks),
        id_map=[
            ProjectPlanDraftIdMap(client_id=client_id, task_id=task_id)
            for client_id, task_id in id_map.items()
        ],
    )


def _plan_task_audit_details(
    db,
    data: ProjectPlanDraftCommitIn,
    id_map: dict[int, int],
) -> list[dict[str, object]]:
    task_by_client_id = {item.client_id: item for item in data.tasks}
    parent_client_ids = {
        item.parent_id for item in data.tasks if item.parent_id is not None
    }
    task_type_names = {
        item.code: item.name for item in db.query(TaskTypeConfig).all()
    }
    instrument_names = {
        item.id: " · ".join(part for part in [item.code, item.name] if part)
        for item in db.query(Instrument).all()
    }
    user_names = {
        item.id: item.display_name or item.username for item in db.query(User).all()
    }

    def reference_name(reference_id: int) -> str:
        draft = task_by_client_id.get(reference_id)
        if draft:
            return draft.name
        task = db.query(Task).filter(Task.id == reference_id).first()
        return task.name if task else f"任务 #{reference_id}"

    return [
        {
            "task_id": id_map[item.client_id],
            "name": item.name.strip(),
            "task_type": (
                "方案签批" if item.is_external_gate
                else "任务组" if item.client_id in parent_client_ids
                else task_type_names.get(item.task_type, item.task_type)
            ),
            "estimated_hours": item.estimated_hours,
            "assignee": user_names.get(item.assignee_id, "未指定负责人") if item.assignee_id else "未指定负责人",
            "instruments": [
                instrument_names.get(instrument_id, f"仪器 #{instrument_id}")
                for instrument_id in item.instrument_ids
            ],
            "predecessors": [reference_name(task_id) for task_id in item.predecessor_ids],
            "parent": reference_name(item.parent_id) if item.parent_id is not None else None,
        }
        for item in data.tasks
    ]


def _validate_task_types(db, data: ProjectPlanDraftCommitIn) -> None:
    parent_client_ids = {
        item.parent_id for item in data.tasks if item.parent_id is not None
    }
    codes = {
        item.task_type
        for item in data.tasks
        if item.client_id not in parent_client_ids
        and not item.is_external_gate
        and item.task_type != "group"
    }
    active_codes = {
        item.code for item in db.query(TaskTypeConfig).filter(
            TaskTypeConfig.code.in_(codes), TaskTypeConfig.is_active.is_(True)
        ).all()
    }
    missing = codes - active_codes
    if missing:
        raise ProjectPlanDraftInvalidError(f"任务类型未启用：{', '.join(sorted(missing))}")


def _validate_references(db, project_id: int, data: ProjectPlanDraftCommitIn) -> None:
    client_ids = {item.client_id for item in data.tasks}
    referenced_ids = {
        reference_id
        for item in data.tasks
        for reference_id in [item.parent_id, *item.predecessor_ids]
        if reference_id is not None
    }
    missing_client_ids = {item_id for item_id in referenced_ids if item_id < 0} - client_ids
    if missing_client_ids:
        raise ProjectPlanDraftInvalidError("草稿引用了不存在的临时任务")
    existing_ids = {item_id for item_id in referenced_ids if item_id > 0}
    if existing_ids:
        valid_existing = {
            row[0] for row in db.query(Task.id).filter(
                Task.project_id == project_id, Task.id.in_(existing_ids)
            ).all()
        }
        if valid_existing != existing_ids:
            raise ProjectPlanDraftInvalidError("草稿引用了其他项目的任务")


def _validate_acyclic_references(db, project_id: int, data: ProjectPlanDraftCommitIn) -> None:
    existing_tasks = db.query(Task.id, Task.parent_id).filter(
        Task.project_id == project_id
    ).all()
    project_task_ids = {task_id for task_id, _ in existing_tasks}

    parent_graph = {
        task_id: {parent_id}
        for task_id, parent_id in existing_tasks
        if parent_id is not None
    }
    for item in data.tasks:
        if item.parent_id is not None:
            parent_graph[item.client_id] = {item.parent_id}
    if _has_cycle(parent_graph):
        raise ProjectPlanDraftInvalidError("父子任务层级不能形成循环")

    dependency_graph: dict[int, set[int]] = {}
    if project_task_ids:
        for task_id, predecessor_id in db.query(
            TaskDependency.task_id, TaskDependency.predecessor_id
        ).filter(TaskDependency.task_id.in_(project_task_ids)).all():
            dependency_graph.setdefault(task_id, set()).add(predecessor_id)
    for item in data.tasks:
        dependency_graph.setdefault(item.client_id, set()).update(item.predecessor_ids)
    if _has_cycle(dependency_graph):
        raise ProjectPlanDraftInvalidError("任务前置关系不能形成循环")


def _has_cycle(graph: dict[int, set[int]]) -> bool:
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(node_id: int) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(visit(reference_id) for reference_id in graph.get(node_id, set())):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in graph if node_id not in visited)


def _resolve_id(value: int | None, id_map: dict[int, int]) -> int | None:
    if value is None or value > 0:
        return value
    return id_map[value]


def _downstream_tasks(db, predecessor_id: int) -> list[Task]:
    dependencies = db.query(TaskDependency).all()
    by_predecessor: dict[int, set[int]] = {}
    for dependency in dependencies:
        by_predecessor.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)
    task_ids: set[int] = set()
    pending = [predecessor_id]
    while pending:
        current = pending.pop()
        for task_id in by_predecessor.get(current, set()):
            if task_id not in task_ids:
                task_ids.add(task_id)
                pending.append(task_id)
    return db.query(Task).filter(Task.id.in_(task_ids)).all() if task_ids else []
