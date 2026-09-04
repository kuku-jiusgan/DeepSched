"""未签批方案下游工时对项目可用窗口的收窄。

签批哪天通过没有依据，把这些工时钉在某个具体时段等于凭空预留一段仪器时间，
签批下来之前谁都用不上，时间轴上还会留下一段无人认领的空白。所以它们不进入
求解器的区间模型——不占具体位置。

但项目结题日是跟客户签的合同日期，这些活在结题前一定要做完，工时必须计入，
否则排程会假装按期完成，等签批通过才暴露延期。做法是把项目的可用窗口按工作
日历往前收窄相应的工时：已排任务必须在收窄后的时点之前完工，装不下就报排程
失败，让人当场改日期或减工时。
"""

from __future__ import annotations

from app.services.scheduler_helpers import datetime_to_units, task_duration_units


def pending_approval_end_bounds(
    forecast_tasks,
    global_prefix_sum,
    horizon_start,
    total_units: int,
    project_end_date_overrides: dict[int, object] | None = None,
) -> dict[int, int]:
    """项目 → 收窄后的完工上界（时间单元）。"""
    pending_units: dict[int, int] = {}
    projects: dict[int, object] = {}
    for task in forecast_tasks:
        deadline = (project_end_date_overrides or {}).get(
            task.project_id,
            task.project.end_date if task.project else None,
        )
        if not task.project or not deadline:
            continue
        pending_units[task.project_id] = (
            pending_units.get(task.project_id, 0) + task_duration_units(task)
        )
        projects[task.project_id] = deadline
    return {
        project_id: _pull_back(
            global_prefix_sum,
            min(total_units, datetime_to_units(projects[project_id], horizon_start)),
            units,
        )
        for project_id, units in pending_units.items()
        if units > 0
    }


def _pull_back(prefix_sum, end_unit: int, required_units: int) -> int:
    """从 end_unit 往前退，直到中间的有效工时够放下 required_units。

    退的是有效工时而不是自然时间：夜间和周末排不了活，按自然时间倒推会把上界
    收得太松，等于没扣够。
    """
    if not prefix_sum or required_units <= 0:
        return end_unit
    safe_end = max(0, min(end_unit, len(prefix_sum) - 1))
    target = prefix_sum[safe_end] - required_units
    for unit in range(safe_end, -1, -1):
        if prefix_sum[unit] <= target:
            return unit
    return 0
