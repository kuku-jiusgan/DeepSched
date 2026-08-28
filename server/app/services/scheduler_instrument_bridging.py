from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model


def instrument_bridge_candidates(tasks, task_dependencies, compatibility):
    """Return manual tasks bracketed by the same assignee and instrument."""
    tasks_by_id = {task.id: task for task in tasks}
    predecessors = defaultdict(list)
    successors = defaultdict(list)
    for task_id, predecessor_id in task_dependencies:
        if task_id in tasks_by_id and predecessor_id in tasks_by_id:
            predecessors[task_id].append(predecessor_id)
            successors[predecessor_id].append(task_id)

    candidates = []
    for task in tasks:
        if (
            getattr(task, "requires_instrument", False)
            or not getattr(task, "requires_human", False)
            or not getattr(task, "assignee_id", None)
        ):
            continue
        for previous_id in predecessors.get(task.id, []):
            for following_id in successors.get(task.id, []):
                previous = tasks_by_id[previous_id]
                following = tasks_by_id[following_id]
                if not _same_assignee(task, previous, following):
                    continue
                previous_ids = {item.id for item in compatibility.get(previous_id, [])}
                following_ids = {item.id for item in compatibility.get(following_id, [])}
                for instrument_id in sorted(previous_ids & following_ids):
                    candidates.append((task.id, previous_id, following_id, instrument_id))
    return candidates


def add_instrument_bridge_intervals(
    model: cp_model.CpModel,
    tasks,
    task_dependencies,
    compatibility,
    task_starts,
    task_ends,
    capacity_intervals,
    presences,
    total_units: int,
) -> list[dict]:
    bridges = []
    for task_id, previous_id, following_id, instrument_id in instrument_bridge_candidates(
        tasks, task_dependencies, compatibility,
    ):
        previous_presence = presences.get((previous_id, instrument_id))
        following_presence = presences.get((following_id, instrument_id))
        if previous_presence is None or following_presence is None:
            continue
        presence = model.NewBoolVar(
            f"bridge_t{task_id}_between_{previous_id}_{following_id}_i{instrument_id}"
        )
        model.AddBoolAnd([previous_presence, following_presence]).OnlyEnforceIf(presence)
        model.AddBoolOr([previous_presence.Not(), following_presence.Not(), presence])
        span = model.NewIntVar(0, total_units, f"bridge_span_t{task_id}_i{instrument_id}")
        model.Add(span == task_ends[task_id] - task_starts[task_id]).OnlyEnforceIf(presence)
        model.Add(span == 0).OnlyEnforceIf(presence.Not())
        interval = model.NewOptionalIntervalVar(
            task_starts[task_id], span, task_ends[task_id], presence,
            f"bridge_iv_t{task_id}_i{instrument_id}",
        )
        capacity_intervals[instrument_id].append(interval)
        bridges.append({
            "task_id": task_id,
            "previous_task_id": previous_id,
            "following_task_id": following_id,
            "instrument_id": instrument_id,
            "presence": presence,
        })
    return bridges


def bridged_instrument_task_ids(
    tasks, task_dependencies, compatibility, instrument_id, top_level_task_id=None,
) -> set[int]:
    """夹在两个"同仪器 + 同负责人"任务之间、会占住该仪器的非仪器任务。"""
    tasks_by_id = {task.id: task for task in tasks}
    return {
        task_id
        for task_id, _previous_id, _following_id, candidate_instrument_id
        in instrument_bridge_candidates(tasks, task_dependencies, compatibility)
        if candidate_instrument_id == instrument_id
        and (
            top_level_task_id is None
            or _top_level_task_id(tasks_by_id[task_id]) == top_level_task_id
        )
    }


def bridged_instrument_hours(
    tasks, task_dependencies, compatibility, instrument_id, top_level_task_id=None,
):
    tasks_by_id = {task.id: task for task in tasks}
    task_ids = bridged_instrument_task_ids(
        tasks, task_dependencies, compatibility, instrument_id, top_level_task_id,
    )
    return sum(
        float(getattr(tasks_by_id[task_id], "est_duration_hours", None) or 4)
        + float(getattr(tasks_by_id[task_id], "switchover_hours", 0) or 0)
        for task_id in task_ids
    )


def _top_level_task_id(task):
    current = task
    visited = set()
    while getattr(current, "parent", None) is not None and current.id not in visited:
        visited.add(current.id)
        current = current.parent
    return current.id


def _same_assignee(task, previous, following) -> bool:
    assignee_id = getattr(task, "assignee_id", None)
    return (
        assignee_id == getattr(previous, "assignee_id", None)
        and assignee_id == getattr(following, "assignee_id", None)
        and getattr(previous, "requires_instrument", False)
        and getattr(following, "requires_instrument", False)
    )
