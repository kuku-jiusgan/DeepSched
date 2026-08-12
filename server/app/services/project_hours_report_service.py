from __future__ import annotations

from datetime import date, datetime, time
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from app.schemas.project_hours_report_schemas import (
    ProjectHoursItemOut,
    ProjectHoursReportOut,
    ProjectHoursTaskOut,
)
from app.services.project_access_service import list_visible_projects
from app.services.project_actual_hours_service import task_actual_hours_map


def build_project_hours_report(
    db,
    user,
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
) -> ProjectHoursReportOut:
    projects = _filter_projects(
        list_visible_projects(db, user),
        start_date,
        end_date,
        keyword,
    )
    leaf_task_ids = {
        task.id for project in projects for task in project.tasks
        if not task.children and not task.is_external_gate
    }
    leaf_actual_hours = task_actual_hours_map(db, leaf_task_ids)
    items = [_project_item(project, leaf_actual_hours) for project in projects]
    items.sort(key=lambda item: item.project_code)
    return ProjectHoursReportOut(
        generated_at=datetime.now(),
        project_count=len(items),
        planned_hours=round(sum(item.planned_hours for item in items), 2),
        actual_hours=round(sum(item.actual_hours for item in items), 2),
        items=items,
    )


def export_project_hours_report(report: ProjectHoursReportOut) -> BytesIO:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "项目汇总"
    _append_header(summary, ["项目编号", "项目名称", "客户", "负责人", "任务数", "总工时(h)", "实际工时(h)", "工时差异(h)"])
    for item in report.items:
        summary.append([
            item.project_code, item.project_name, item.client_name or "", item.manager_name or "",
            item.task_count, item.planned_hours, item.actual_hours, item.variance_hours,
        ])
    detail = workbook.create_sheet("任务明细")
    _append_header(detail, ["项目编号", "项目名称", "顶级任务", "任务名称", "层级", "负责人", "状态", "总工时(h)", "实际工时(h)"])
    for item in report.items:
        for task in item.tasks:
            detail.append([
                item.project_code, item.project_name, task.top_level_task_name,
                f"{'  ' * task.depth}{task.task_name}", task.depth + 1,
                task.assignee_name or "", task.status, task.planned_hours, task.actual_hours,
            ])
    _format_sheet(summary, [16, 24, 20, 16, 10, 14, 14, 14])
    _format_sheet(detail, [16, 24, 24, 32, 10, 16, 14, 14, 14])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _project_item(project, leaf_actual_hours: dict[int, float]) -> ProjectHoursItemOut:
    tasks = [task for task in project.tasks if not task.is_external_gate]
    task_by_parent: dict[int | None, list] = {}
    for task in tasks:
        task_by_parent.setdefault(task.parent_id, []).append(task)
    for children in task_by_parent.values():
        children.sort(key=lambda task: (task.plan_order, task.id))
    actual_cache: dict[int, float] = {}

    def actual_hours(task) -> float:
        if task.id not in actual_cache:
            children = task_by_parent.get(task.id, [])
            actual_cache[task.id] = sum(actual_hours(child) for child in children) if children else leaf_actual_hours.get(task.id, 0.0)
        return actual_cache[task.id]

    rows: list[ProjectHoursTaskOut] = []

    def append_task(task, depth: int, top_name: str) -> None:
        rows.append(ProjectHoursTaskOut(
            task_id=task.id,
            parent_id=task.parent_id,
            task_name=task.name,
            top_level_task_name=top_name,
            assignee_name=task.assignee_name,
            status=task.status,
            depth=depth,
            planned_hours=round(float(task.est_duration_hours or 0), 2),
            actual_hours=round(actual_hours(task), 2),
        ))
        for child in task_by_parent.get(task.id, []):
            append_task(child, depth + 1, top_name)

    top_tasks = task_by_parent.get(None, [])
    for task in top_tasks:
        append_task(task, 0, task.name)
    planned = round(sum(float(task.est_duration_hours or 0) for task in top_tasks), 2)
    actual = round(sum(actual_hours(task) for task in top_tasks), 2)
    return ProjectHoursItemOut(
        project_id=project.id,
        project_code=project.code,
        project_name=project.name,
        client_name=project.client_name,
        manager_name=project.manager_name,
        task_count=len(rows),
        planned_hours=planned,
        actual_hours=actual,
        variance_hours=round(actual - planned, 2),
        tasks=rows,
    )


def _filter_projects(
    projects,
    start_date: date | None,
    end_date: date | None,
    keyword: str | None,
):
    start_at = datetime.combine(start_date, time.min) if start_date else None
    end_at = datetime.combine(end_date, time.max) if end_date else None
    normalized_keyword = (keyword or "").strip().lower()
    return [
        project for project in projects
        if (start_at is None or project.start_date is None or project.start_date >= start_at)
        and (end_at is None or project.start_date is None or project.start_date <= end_at)
        and (
            not normalized_keyword
            or normalized_keyword in project.code.lower()
            or normalized_keyword in project.name.lower()
            or normalized_keyword in (project.client_name or "").lower()
            or normalized_keyword in (project.manager_name or "").lower()
        )
    ]


def _append_header(sheet, values: list[str]) -> None:
    sheet.append(values)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2563EB")


def _format_sheet(sheet, widths: list[int]) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
