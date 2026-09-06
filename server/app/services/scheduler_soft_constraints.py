"""前置依赖硬约束，以及喂给目标函数的各类软惩罚项。

这里只负责构造惩罚变量，权重的取舍全部集中在 scheduler_objective 里。
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from app.services.scheduler_helpers import datetime_to_units


def add_precedence_constraints(
    model: cp_model.CpModel,
    *,
    predecessor_ends: dict[int, int],
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

    # predecessor_ends 是建模开始前一次装载好的全量前置完工时间，这里按原逻辑
    # 筛出"不在求解集合里"的那部分。建模过程中不再查库。
    missing_pred_ends = {
        pred_id: predecessor_ends[pred_id]
        for pred_id in missing_pred_ids
        if pred_id in predecessor_ends
    }

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


def add_first_start_constraint(
    model: cp_model.CpModel,
    *,
    task_starts,
    first_start_task_id: int | None,
) -> None:
    """指定任务必须第一个开始，其余任务都不得排在它之前。

    暂停并切换用它落实一条业务语义：人既然已经决定切过去，就是现在要做这个任务，
    不存在"先干点别的再说"。此前靠目标函数的整体最优近似，实测切换之后周一早上先
    排了另一个项目的方案撰写（同一负责人、不占仪器、结题日更早），接替任务被推到
    两个半小时后才开始——从目标函数看合理，从业务上看这次切换就落空了。

    约束管的是**开始时刻**而不是完工时刻：其余任务只是不许比它更早开始，并不需要
    等它整个做完。后者会把闭包重新串成一条硬链，正是此前让排程判定不可行的错误。
    """
    if first_start_task_id is None or first_start_task_id not in task_starts:
        return
    anchor = task_starts[first_start_task_id]
    # 按任务号排序：集合/字典的遍历顺序会直接落进模型 proto，同一道题两次构造
    # 必须逐字节相同。
    for task_id, start in sorted(task_starts.items()):
        if task_id != first_start_task_id:
            model.Add(start >= anchor)


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
    # 按任务号排序再求和。集合的遍历顺序不稳定，而这是权重最高的目标项
    # （×100_000），求和顺序会直接落进目标函数的 proto 里。
    early_start_penalties = [
        task_starts[task_id]
        for task_id in sorted(early_start_task_ids or ())
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
    # 排序遍历：这里在循环里建变量，顺序一变，其后所有 proto 索引全部错位。
    for task_id in sorted(stability_task_ids or ()):
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
