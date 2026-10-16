"""按暂停切换的完整求解任务范围计算窗口。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.services.approval_gate_graph_service import unapproved_gate_context
from app.services.schedule_rule_service import get_solver_constraints
from app.services.schedule_working_time_service import advance_working_hours
from app.services.scheduler_data import load_scheduler_data, load_task_children
from app.services.scheduler_helpers import (
    TIME_UNIT_MINUTES, build_compatibility, build_dependencies,
    time_horizon, units_to_datetime,
)
from app.services.scheduler_predecessor_bounds import load_missing_predecessor_ends
from app.services.scheduler_cross_project_setup import cross_project_setup_units
from app.services.scheduler_task_duration import remaining_workload_units
from app.services.task_pause_switch_context_service import PauseSwitchContext


_logger = logging.getLogger(__name__)


def solver_horizon_end(
    db, context: PauseSwitchContext, *, relax_project_deadlines: bool = False,
) -> datetime:
    """同时容纳项目日期、外部前置和全部剩余工时。

    使用求解器的权威任务加载器，包含同项目其他分支的待签批下游。
    从最晚释放时刻起预留串行工时，并按相关资源工作日历换算；这是窗口
    估算，不替代求解器对固定占用、维护、资源和结题日期的实际校验。
    项目时间窗启用且全部任务都有结题日期时，正式求解只需覆盖最晚结题日期。
    延期诊断和没有完整结题日期的求解使用完整工时窗口，不修改真实结题日期。
    """
    project_ids = {entry.task.project_id for entry in context.queue}
    tasks, instruments = load_scheduler_data(
        db, project_ids=sorted(project_ids), task_ids=sorted(context.task_ids),
        replan_task_ids=context.task_ids, occupancy_project_ids=project_ids,
    )
    if not tasks:
        raise ValueError("暂停切换没有可求解任务")
    horizon_start, _, _ = time_horizon(start_at=context.switch_time)
    constraints = get_solver_constraints(db)
    deadlines = [task.project.end_date for task in tasks if task.project.end_date]
    if (
        not relax_project_deadlines and constraints["project_window"].is_enabled
        and len(deadlines) == len(tasks)
    ):
        # 硬结题日期之外没有合法解，扩大这一段只会增加无用的搜索空间。
        # 已过期项目仍交给求解器校验；模型窗口本身必须保持非空。
        horizon_end = max(
            horizon_start + timedelta(minutes=TIME_UNIT_MINUTES), *deadlines,
        )
        _logger.info(
            "pause_switch_horizon queue_tasks=%s solver_tasks=%s "
            "deadline_bounded=True horizon_end=%s",
            len(context.task_ids), len(tasks), horizon_end,
        )
        return horizon_end
    release_at = _latest_release(db, tasks, horizon_start, context.queue_end)
    workload_units = sum(
        remaining_workload_units(task, context.remaining_duration_minutes)
        for task in tasks
    )
    workload_units += len(tasks) * cross_project_setup_units(constraints)
    compat = build_compatibility(
        tasks, instruments, constraints["capability_matching"].is_enabled,
    )
    resource_ids = {None} | {
        instrument.id for candidates in compat.values() for instrument in candidates
    }
    workload_end = max(
        advance_working_hours(
            db, release_at, workload_units * TIME_UNIT_MINUTES / 60, instrument_id,
        )
        for instrument_id in resource_ids
    )
    horizon_end = max([workload_end + timedelta(days=2), *deadlines])
    _logger.info(
        "pause_switch_horizon queue_tasks=%s solver_tasks=%s release_at=%s "
        "workload_units=%s deadline_bounded=False horizon_end=%s",
        len(context.task_ids), len(tasks), release_at, workload_units, horizon_end,
    )
    return horizon_end


def _latest_release(db, tasks, horizon_start, queue_end) -> datetime:
    task_ids = {task.id for task in tasks}
    dependencies = build_dependencies(tasks, load_task_children(db, tasks))
    predecessor_ends = load_missing_predecessor_ends(
        db, {pred_id for _, pred_id in dependencies if pred_id not in task_ids},
        horizon_start,
    )
    approval_bounds, _ = unapproved_gate_context(db, tasks)
    project_starts = [task.project.start_date for task in tasks if task.project.start_date]
    return max(
        horizon_start, queue_end, *project_starts, *approval_bounds.values(),
        *(units_to_datetime(end, horizon_start) for end in predecessor_ends.values()),
    )
