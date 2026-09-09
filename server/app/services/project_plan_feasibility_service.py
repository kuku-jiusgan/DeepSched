from __future__ import annotations

from app.models import Project, Task
from app.services.project_plan_errors import ProjectPlanInvalidError


def validate_immediate_approval_feasibility(
    db,
    *,
    project: Project,
    replan_tasks: list[Task],
    released_slot_ids: set[int],
) -> None:
    """Probe the replan with pending approval work treated as immediate."""
    if not replan_tasks:
        return
    from app.services.scheduler import SchedulerService

    task_ids = {task.id for task in replan_tasks}
    project_ids = {task.project_id for task in replan_tasks if task.project_id}
    # 立即签批按全厂未收尾项目同时释放待签批工时，避免跨项目产能被高估。
    occupancy_project_ids = {
        row[0] for row in db.query(Project.id).filter(
            Project.status.notin_(("completed", "cancelled", "archived")),
        ).all()
    }
    probe_savepoint = db.begin_nested()
    try:
        result = SchedulerService(db).generate(
            project_ids=sorted(project_ids),
            task_ids=sorted(task_ids),
            current_project_id=project.id,
            replaceable_task_ids=task_ids,
            released_slot_ids=released_slot_ids,
            commit=False,
            feasibility_only=True,
            include_pending_approval_tasks=True,
            emit_advance_notifications=False,
            include_failure_diagnostics=True,
            occupancy_project_ids=occupancy_project_ids,
        )
    finally:
        probe_savepoint.rollback()
    if result.get("status") == "ok":
        return
    failure = result.get("schedule_failure")
    raise ProjectPlanInvalidError(
        _immediate_approval_failure_message(project, result),
        schedule_failure=_immediate_approval_failure_diagnostic(project, failure),
    )


def _immediate_approval_failure_message(project: Project, result: dict) -> str:
    label = f"{project.code} {project.name}" if project.code else project.name
    deadline = (
        project.end_date.strftime("%Y-%m-%d %H:%M")
        if project.end_date else "当前结题日期"
    )
    failure = result.get("schedule_failure") or {}
    task_name = (failure.get("window") or {}).get("task_name")
    workload_label = f"任务【{task_name}】及后续任务" if task_name else "方法验证及后续任务"
    return (
        f"项目【{label}】即使立即完成签批，{workload_label}也无法在 "
        f"{deadline} 结题日前完成，请先延长项目结题日期或调整排程资源。"
    )


def _immediate_approval_failure_diagnostic(project: Project, failure: dict | None) -> dict | None:
    """Add the immediate-approval context while retaining solver diagnostics."""
    if not failure:
        return None
    diagnostic = dict(failure)
    diagnostic["title"] = "立即签批校验未通过"
    diagnostic["summary"] = (
        f"项目【{project.name}】即使立即完成签批，方法验证及后续任务仍无法在结题日前完成。"
    )
    diagnostic["project_id"] = project.id
    diagnostic["project_label"] = (
        f"{project.code} · {project.name}" if project.code else project.name
    )
    return diagnostic
