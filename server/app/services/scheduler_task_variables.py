"""每个任务的决策变量与自身约束。

一个任务对应一组变量：开始/结束时刻、物理跨度、逾期量，以及每台候选仪器
上的可选区间。约束保证跨度内的有效工作时间恰好等于剩余时长，并把任务限制
在所属项目的计划窗口内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, Tuple

from ortools.sat.python import cp_model

from app.services.scheduler_failure_diagnostics import build_task_window_failure
from app.services.scheduler_helpers import (
    TIME_UNIT_MINUTES,
    datetime_to_units,
    optional_time_domain,
    to_units,
)
from app.services.scheduler_split_tasks import add_split_task_variables
from app.services.scheduler_task_duration import remaining_duration_units


@dataclass
class TaskVariables:
    """一次求解中所有任务的决策变量集合。"""

    capacity_intervals: Dict[int, list] = field(default_factory=dict)
    presences: Dict[Tuple[int, int], cp_model.IntVar] = field(default_factory=dict)
    inst_starts: Dict[Tuple[int, int], cp_model.IntVar] = field(default_factory=dict)
    inst_ends: Dict[Tuple[int, int], cp_model.IntVar] = field(default_factory=dict)
    task_starts: Dict[int, cp_model.IntVar] = field(default_factory=dict)
    task_ends: Dict[int, cp_model.IntVar] = field(default_factory=dict)
    task_intervals: Dict[int, cp_model.IntervalVar] = field(default_factory=dict)
    task_tardiness: Dict[int, cp_model.IntVar] = field(default_factory=dict)
    split_unit_presences: Dict[Tuple[int, int, int], cp_model.IntVar] = field(default_factory=dict)


def build_task_variables(
    model: cp_model.CpModel,
    *,
    tasks,
    instruments,
    compat,
    constraints,
    approval_bounds,
    horizon_start,
    total_units: int,
    global_prefix_sum,
    instrument_prefix_sums,
    fixed_slots,
    remaining_duration_minutes,
) -> tuple[TaskVariables, dict | None]:
    """建好全部任务变量；某个任务放不下时返回错误响应。"""
    variables = TaskVariables()
    for instrument in instruments:
        variables.capacity_intervals[instrument.id] = []

    for t in tasks:
        dur = to_units(t.est_duration_hours or 4)
        if t.switchover_hours and t.switchover_hours > 0:
            dur += to_units(t.switchover_hours)
        dur = remaining_duration_units(
            t,
            dur,
            fixed_slots,
            global_prefix_sum,
            instrument_prefix_sums,
            horizon_start,
            total_units,
            remaining_duration_minutes,
        )

        # Compute project-level hard constraint window
        p_start_unit = 0
        p_end_unit = total_units
        if t.project and constraints["project_window"].is_enabled:
            if t.project.start_date:
                p_start_u = datetime_to_units(t.project.start_date, horizon_start)
                p_start_unit = max(0, p_start_u)
            if t.project.end_date:
                p_end_u = datetime_to_units(t.project.end_date, horizon_start)
                p_end_unit = min(total_units, p_end_u)
        approval_bound = approval_bounds.get(t.id)
        if approval_bound:
            p_start_unit = max(
                p_start_unit,
                datetime_to_units(approval_bound, horizon_start),
            )

        # Guard: task duration exceeds available project window
        if p_start_unit + dur > p_end_unit:
            earliest_start = horizon_start + timedelta(
                minutes=p_start_unit * TIME_UNIT_MINUTES,
            )
            return variables, {
                "status": "error",
                **build_task_window_failure(t, earliest_start, dur),
            }

        # Constrain task start/end within project boundaries
        task_start_max = p_end_unit - dur
        task_end_min = p_start_unit + dur
        variables.task_starts[t.id] = model.NewIntVar(
            p_start_unit,
            task_start_max,
            f"start_t{t.id}",
        )
        variables.task_ends[t.id] = model.NewIntVar(
            task_end_min,
            p_end_unit,
            f"end_t{t.id}",
        )
        variables.task_tardiness[t.id] = model.NewIntVar(0, total_units, f"tardy_t{t.id}")

        # Physical span can stretch beyond dur to accommodate night breaks
        task_span = model.NewIntVar(
            dur,
            p_end_unit - p_start_unit,
            f"span_t{t.id}",
        )
        model.Add(variables.task_ends[t.id] - variables.task_starts[t.id] == task_span)

        task_interval = model.NewIntervalVar(
            variables.task_starts[t.id], task_span, variables.task_ends[t.id], f"task_iv_t{t.id}"
        )
        variables.task_intervals[t.id] = task_interval

        candidates = compat.get(t.id, [])
        if not candidates:
            # Manual task: apply prefix-sum constraint to respect night window
            start_work_acc = model.NewIntVar(0, total_units, f"start_acc_t{t.id}")
            end_work_acc = model.NewIntVar(0, total_units, f"end_acc_t{t.id}")
            model.AddElement(variables.task_starts[t.id], global_prefix_sum, start_work_acc)
            model.AddElement(variables.task_ends[t.id], global_prefix_sum, end_work_acc)
            model.Add(end_work_acc - start_work_acc == dur)
            continue

        if t.allow_split:
            is_valid_split = add_split_task_variables(
                model,
                t,
                candidates,
                dur,
                p_start_unit,
                p_end_unit,
                task_start_max,
                task_end_min,
                total_units,
                instrument_prefix_sums,
                variables.task_starts[t.id],
                variables.task_ends[t.id],
                variables.presences,
                variables.inst_starts,
                variables.inst_ends,
                variables.capacity_intervals,
                variables.split_unit_presences,
            )
            if not is_valid_split:
                return variables, {
                    "status": "error",
                    "message": f"排程失败：任务【{t.name}】没有足够的可拆分工作时段。",
                }
            continue

        alt_starts = []
        alt_ends = []
        alt_presences = []
        for inst in candidates:
            instrument_prefix_sum = instrument_prefix_sums[inst.id]
            key = (t.id, inst.id)
            variables.presences[key] = model.NewBoolVar(f"presence_t{t.id}_i{inst.id}")
            inst_start = model.NewIntVarFromDomain(
                optional_time_domain(p_start_unit, task_start_max),
                f"start_t{t.id}_i{inst.id}",
            )
            inst_end = model.NewIntVarFromDomain(
                optional_time_domain(task_end_min, p_end_unit),
                f"end_t{t.id}_i{inst.id}",
            )
            inst_span = model.NewIntVar(
                0,
                p_end_unit - p_start_unit,
                f"span_t{t.id}_i{inst.id}",
            )
            model.Add(inst_end - inst_start == inst_span)

            inst_iv = model.NewOptionalIntervalVar(
                inst_start, inst_span, inst_end, variables.presences[key], f"iv_t{t.id}_i{inst.id}"
            )
            variables.capacity_intervals[inst.id].append(inst_iv)
            alt_starts.append(inst_start)
            alt_ends.append(inst_end)
            alt_presences.append(variables.presences[key])

            # Store per-instrument start/end for cross-project constraints
            variables.inst_starts[key] = inst_start
            variables.inst_ends[key] = inst_end

            # Bidirectional link: per-instrument start/end == task start/end
            model.Add(variables.task_starts[t.id] == inst_start).OnlyEnforceIf(variables.presences[key])
            model.Add(variables.task_ends[t.id] == inst_end).OnlyEnforceIf(variables.presences[key])
            # When not selected, force instrument start/end to zero
            model.Add(inst_start == 0).OnlyEnforceIf(variables.presences[key].Not())
            model.Add(inst_end == 0).OnlyEnforceIf(variables.presences[key].Not())

            # Prefix-sum constraint: effective working time within span == dur
            start_work_acc = model.NewIntVar(0, total_units, f"start_acc_t{t.id}_i{inst.id}")
            end_work_acc = model.NewIntVar(0, total_units, f"end_acc_t{t.id}_i{inst.id}")
            model.AddElement(inst_start, instrument_prefix_sum, start_work_acc)
            model.AddElement(inst_end, instrument_prefix_sum, end_work_acc)
            model.Add(end_work_acc - start_work_acc == dur).OnlyEnforceIf(variables.presences[key])

        # Exactly one instrument assigned
        model.AddExactlyOne(alt_presences)
    return variables, None
