"""前置依赖硬约束，以及喂给目标函数的各类软惩罚项。

这里只负责构造惩罚变量，权重的取舍全部集中在 scheduler_objective 里。
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from app.services.scheduler_helpers import datetime_to_units
from app.services.scheduler_predecessor_bounds import load_missing_predecessor_ends


def add_precedence_constraints(
    model: cp_model.CpModel,
    db,
    *,
    task_deps,
    task_starts,
    task_ends,
    horizon_start,
    precedence_enabled: bool,
    additional_dependency_gaps,
) -> dict[int, int]:
    """加上前置依赖约束，返回模型外前置任务的固定结束时间下界。"""
    # Precedence constraints (DAG)
    # Bug 1 fix: handle frozen/missing predecessors as constant bounds
    missing_pred_ids = {
        pred_id
        for _, pred_id in task_deps
        if pred_id not in task_starts
    }

    missing_pred_ends = load_missing_predecessor_ends(
        db, missing_pred_ids, horizon_start,
    )

    if precedence_enabled:
        for tid, pred_id in task_deps:
            if pred_id in task_starts and tid in task_starts:
                gap_units = (additional_dependency_gaps or {}).get((tid, pred_id), 0)
                model.Add(task_starts[tid] >= task_ends[pred_id] + gap_units)

        # Frozen/missing predecessors: apply constant lower-bound
        for task_id, predecessor_id in task_deps:
            if (
                predecessor_id in missing_pred_ends
                and task_id in task_starts
            ):
                model.Add(
                    task_starts[task_id]
                    >= missing_pred_ends[predecessor_id]
                )

    return missing_pred_ends


def build_dependency_gap_penalties(
    model: cp_model.CpModel,
    *,
    task_deps,
    task_starts,
    task_ends,
    total_units: int,
) -> list:
    """依赖任务之间的空档惩罚：前置做完就尽量接着做。"""
    dependency_gap_penalties = []
    for task_id, predecessor_id in task_deps:
        if task_id not in task_starts or predecessor_id not in task_ends:
            continue
        gap = model.NewIntVar(0, total_units, f"dependency_gap_{predecessor_id}_{task_id}")
        model.Add(gap >= task_starts[task_id] - task_ends[predecessor_id])
        dependency_gap_penalties.append(gap)

    return dependency_gap_penalties


def build_early_start_penalties(task_starts, early_start_task_ids) -> list:
    """资源尽早释放：对指定任务的开始时刻求和作为惩罚。"""
    early_start_penalties = [
        task_starts[task_id]
        for task_id in (early_start_task_ids or set())
        if task_id in task_starts
    ]

    return early_start_penalties


def build_project_instrument_penalties(
    model: cp_model.CpModel,
    *,
    tasks,
    instruments,
    presences,
) -> list:
    """一个项目铺开到多台仪器的惩罚。"""
    # === Project split penalty: discourage spreading one project across many instruments ===
    project_to_tasks = {}
    for t in tasks:
        if t.requires_instrument and t.project_id:
            if t.project_id not in project_to_tasks:
                project_to_tasks[t.project_id] = []
            project_to_tasks[t.project_id].append(t)

    project_inst_used_vars = []
    for pid, p_tasks in project_to_tasks.items():
        for inst in instruments:
            used_var = model.NewBoolVar(f"used_p{pid}_i{inst.id}")
            task_presences = []
            for t in p_tasks:
                key = (t.id, inst.id)
                if key in presences:
                    task_presences.append(presences[key])
            if task_presences:
                model.AddMaxEquality(used_var, task_presences)
                project_inst_used_vars.append(used_var)

    return project_inst_used_vars


def add_milestone_tardiness(
    model: cp_model.CpModel,
    *,
    tasks,
    task_ends,
    task_tardiness,
    horizon_start,
    total_units: int,
    milestone_enabled: bool,
) -> None:
    """里程碑逾期量：软约束，只进目标函数不阻断求解。"""
    # Milestone deadlines → tardiness
    for t in tasks:
        if milestone_enabled and t.milestone_id and t.milestone:
            deadline = datetime_to_units(t.milestone.due_date, horizon_start)
            if 0 <= deadline <= total_units:
                model.Add(task_tardiness[t.id] >= task_ends[t.id] - deadline)


def build_stability_penalties(
    model: cp_model.CpModel,
    *,
    stability_task_ids,
    original_schedule_windows,
    task_starts,
    horizon_start,
    total_units: int,
) -> list:
    """与原计划开始时间的偏差惩罚，用于抑制被动任务漂移。"""
    stability_penalties = []
    for task_id in stability_task_ids or set():
        old_window = original_schedule_windows.get(task_id)
        if task_id not in task_starts or not old_window:
            continue
        old_start_unit = max(0, min(
            total_units,
            datetime_to_units(old_window[0], horizon_start),
        ))
        deviation = model.NewIntVar(0, total_units, f"stability_t{task_id}")
        model.AddAbsEquality(deviation, task_starts[task_id] - old_start_unit)
        stability_penalties.append(deviation)

    return stability_penalties


def sibling_cohesion_weight(rule) -> int:
    if not rule.is_enabled:
        return 0
    raw_weight = (rule.params or {}).get("weight", 1.0)
    try:
        weight = float(raw_weight)
    except (TypeError, ValueError):
        weight = 1.0
    return round(max(0.0, min(10.0, weight)) * 100)
