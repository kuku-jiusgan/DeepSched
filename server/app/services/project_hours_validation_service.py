from datetime import datetime

from app.models import Project, Task
import logging


_logger = logging.getLogger(__name__)

# 浮点比较的容差，避免同一份数据前后算出的缺口因末位误差被判成"变差"。
_DEFICIT_TOLERANCE_HOURS = 0.01


class ProjectHoursExceededError(Exception):
    pass


class ProjectWindowCapacityError(Exception):
    pass


def project_window_capacity_deficit(db, start_date, end_date, hours) -> float:
    """项目工时相对自身时间窗物理容量的缺口，装得下时返回 0。

    容量按工作日历统计，夜间、周末和节假日不计入。开始日期早于今天时从今天
    算起——已经过去的时间不可能再用来做这个项目。这里不扣除其他项目的占用，
    是最宽松的判定：假设仪器完全归它独占。连这样都放不下的项目，无论怎么排
    都不可能成功，应当在保存项目信息时就拦下，而不是拖到排程失败才发现。

    结题日已经过去的项目不适用：这时候没有任何工时可排，校验挡住的只是历史
    数据维护（比如给往期项目补填预计工时），超期本身另有延期状态在提示。
    """
    if not start_date or not end_date or not hours:
        return 0.0
    window_start = max(start_date, datetime.now())
    if end_date <= window_start:
        return 0.0
    from app.services.schedule_working_time_service import working_hours_between

    capacity = working_hours_between(db, window_start, end_date, None)
    return max(0.0, float(hours) - capacity)


def validate_project_window_capacity(
    db, start_date, end_date, hours, previous_deficit: float = 0.0,
) -> None:
    """时间窗装不下项目工时时报错。

    previous_deficit 是保存前的缺口。已经不合格的项目允许继续修改，只要这次
    保存没有让缺口变大——否则用户会被卡死在项目页上，连延长结题日都做不了。
    """
    deficit = project_window_capacity_deficit(db, start_date, end_date, hours)
    if deficit <= previous_deficit + _DEFICIT_TOLERANCE_HOURS:
        return
    window_start = max(start_date, datetime.now())
    capacity = max(0.0, float(hours) - deficit)
    raise ProjectWindowCapacityError(
        f"项目时间窗放不下 {format_hours(float(hours))} 小时工时："
        f"{window_start:%Y-%m-%d} 到结题日 {end_date:%Y-%m-%d} 之间只有 "
        f"{format_hours(capacity)} 小时有效工作时间（已扣除夜间、周末和节假日），"
        f"还差 {format_hours(deficit)} 小时。请延长结题日期或下调工时。"
    )


def validate_project_estimated_hours(db, project_id: int) -> None:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or project.estimated_hours is None:
        return

    total_hours = project_top_level_task_hours(db, project_id)
    _logger.warning(
        "项目工时校验: project_id=%s code=%s estimated_hours=%s "
        "top_level_hours=%s",
        project.id,
        project.code,
        project.estimated_hours,
        total_hours,
    )
    if total_hours <= float(project.estimated_hours):
        return

    raise ProjectHoursExceededError(
        f"项目任务总耗时 {format_hours(total_hours)}h 已超过项目预计工时 "
        f"{format_hours(float(project.estimated_hours))}h"
    )


def validate_projects_estimated_hours(db, project_ids: set[int]) -> None:
    for project_id in sorted(project_ids):
        validate_project_estimated_hours(db, project_id)


def project_top_level_task_hours(db, project_id: int) -> float:
    tasks = db.query(Task).filter(
        Task.project_id == project_id,
        Task.parent_id.is_(None),
    ).all()
    return sum(float(task.est_duration_hours or 0) for task in tasks)


def format_hours(value: float) -> str:
    return f"{value:g}"
