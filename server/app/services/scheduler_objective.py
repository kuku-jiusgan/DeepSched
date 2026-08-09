from __future__ import annotations

from datetime import datetime

from ortools.sat.python import cp_model


def add_scheduler_objective(
    model: cp_model.CpModel,
    tasks,
    task_starts,
    task_ends,
    task_tardiness,
    switch_penalties,
    project_inst_used_vars,
    total_units: int,
    sibling_group_completion_weight: int,
    sibling_counts: dict[int, int],
    stability_penalties: list | None = None,
    dependency_gap_penalties: list | None = None,
) -> None:
    stability_penalties = stability_penalties or []
    dependency_gap_penalties = dependency_gap_penalties or []
    weighted_tardiness = [
        task_tardiness[task.id] * int(task.priority_weight * 10)
        for task in tasks
    ]
    makespan = model.NewIntVar(0, total_units, "makespan")
    model.AddMaxEquality(makespan, [task_ends[task.id] for task in tasks])
    spans = [task_ends[task.id] - task_starts[task.id] for task in tasks]

    max_project_priority = max(
        (int(task.project.priority or 999) for task in tasks if task.project),
        default=999,
    )
    ordered_tasks = sorted(tasks, key=lambda task: (
        task.project.end_date if task.project and task.project.end_date else datetime.max,
        int(task.project.priority or 999) if task.project else 999,
        task.created_at or datetime.min,
        task.id,
    ))
    priority_completion = []
    for index, task in enumerate(ordered_tasks):
        project_priority = int(task.project.priority or 999) if task.project else 999
        project_weight = max(1, max_project_priority - project_priority + 1)
        stable_weight = len(ordered_tasks) - index
        priority_completion.append(task_ends[task.id] * project_weight * stable_weight)

    tasks_by_parent = {}
    for task in tasks:
        if task.parent_id is not None:
            tasks_by_parent.setdefault(task.parent_id, []).append(task)

    parent_group_completions = []
    for parent_id, sibling_tasks in tasks_by_parent.items():
        if sibling_counts.get(parent_id, len(sibling_tasks)) < 2:
            continue
        if len(sibling_tasks) == 1:
            parent_group_completions.append(task_ends[sibling_tasks[0].id])
            continue
        group_completion = model.NewIntVar(
            0,
            total_units,
            f"parent_{parent_id}_completion",
        )
        model.AddMaxEquality(
            group_completion,
            [task_ends[task.id] for task in sibling_tasks],
        )
        parent_group_completions.append(group_completion)

    model.Minimize(
        sum(stability_penalties) * 10_000
        + (sum(weighted_tardiness) + makespan) * 1000
        + sum(priority_completion) * 10
        + sum(parent_group_completions) * sibling_group_completion_weight
        + sum(dependency_gap_penalties) * 500
        + sum(spans)
        + sum(switch_penalties) * 500
        + sum(project_inst_used_vars) * 30
    )
