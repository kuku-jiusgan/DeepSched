from app.models import Task
from app.services.audit_log_service import record_audit_log, structured_audit_detail


class TaskReorderInvalidError(Exception):
    pass


def reorder_project_tasks(
    db,
    project_id: int,
    parent_id: int | None,
    task_ids: list[int],
    actor_name: str | None = None,
) -> None:
    query = db.query(Task).filter(Task.project_id == project_id)
    query = query.filter(Task.parent_id.is_(None)) if parent_id is None else query.filter(Task.parent_id == parent_id)
    siblings = query.all()
    if len(task_ids) != len(set(task_ids)) or set(task_ids) != {task.id for task in siblings}:
        raise TaskReorderInvalidError("任务排序范围不正确，请刷新后重试")
    task_by_id = {task.id: task for task in siblings}
    for index, task_id in enumerate(task_ids):
        task_by_id[task_id].plan_order = index
    if actor_name:
        project = siblings[0].project if siblings else None
        project_display = " · ".join(
            part for part in [project.code if project else None, project.name if project else None] if part
        ) or f"项目 #{project_id}"
        names = [task_by_id[task_id].name for task_id in task_ids]
        record_audit_log(
            db,
            actor_name,
            "task_reordered",
            "project",
            project_id,
            structured_audit_detail(
                "task",
                f"调整项目【{project_display}】任务顺序：{'、'.join(names[:3])}{'等' if len(names) > 3 else ''}",
                project_display,
                context={"parent_id": parent_id, "task_ids": task_ids, "task_names": names},
            ),
        )
    db.commit()
