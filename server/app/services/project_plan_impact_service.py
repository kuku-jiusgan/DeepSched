from __future__ import annotations

from datetime import datetime

from app.models import Task, TimeSlot
from app.schemas.schemas import InsertOrderImpact, ProjectScheduleImpact


def project_completions(db, project_ids: set[int]) -> dict[int, datetime]:
    if not project_ids:
        return {}
    slots = db.query(TimeSlot).join(Task).filter(
        Task.project_id.in_(project_ids),
        TimeSlot.lifecycle_status == "active",
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


def build_project_impacts(
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
        if not impact.is_insert_task:
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


def project_impact_message(impacts: list[ProjectScheduleImpact]) -> str:
    if not impacts:
        return "需要移动同优先级或低优先级的未开始项目任务，请确认排程影响"
    details = []
    for impact in impacts:
        start_time = impact.new_start.strftime("%Y-%m-%d %H:%M") if impact.new_start else "暂无"
        deadline = (
            f"超过结题日期 {impact.overdue_hours:g} 小时"
            if impact.exceeds_end_date else "未超过结题日期"
        )
        details.append(
            f"项目【{impact.project_code} {impact.project_name}】"
            f"预计顺延 {max(0, impact.delay_hours):g} 小时，"
            f"调整后开始时间为 {start_time}，{deadline}"
        )
    return "需要移动同优先级或低优先级的未开始项目任务，请确认影响：" + "；".join(details)


def _hours_between(start: datetime | None, end: datetime | None) -> float:
    if start is None or end is None:
        return 0
    return (end - start).total_seconds() / 3600
