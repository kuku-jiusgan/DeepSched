from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from app.services.scheduler_helpers import task_duration_hours


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


def scheduled_bridge_task_ids(tasks, instrument_id: int) -> set[int]:
    """按该仪器的时间槽队列判定桥接，跨项目。

    依赖边只存在于同一项目内部，沿依赖边找前后任务只能识别项目内的桥接。同一台
    仪器上可能排着多个项目、同一个负责人的任务，例如「A-方法开发 → A-方案撰写 →
    B-方法验证」，A 的方案撰写同样占住了这台仪器。已排定的时间槽顺序是已知的，
    直接按仪器时间轴判定即可覆盖跨项目的情况。

    判定：非仪器任务前后**紧邻**的仪器任务同属一个负责人时构成桥接。取紧邻而非
    任意前后，中间若插着别人的仪器任务，紧邻关系自然不成立，就不算占用。
    """
    instrument_spans = _instrument_task_spans(tasks, instrument_id)
    bridged: set[int] = set()
    for start, end, task in _manual_task_spans(tasks):
        previous = _last_before(instrument_spans, start)
        following = _first_after(instrument_spans, end)
        if previous is None or following is None:
            continue
        if _same_assignee(task, previous, following):
            bridged.add(task.id)
    return bridged


def _active_span(task, instrument_id=None):
    slots = [
        slot for slot in (getattr(task, "time_slots", []) or [])
        if getattr(slot, "lifecycle_status", "active") == "active"
        and slot.plan_start and slot.plan_end
        and (instrument_id is None or slot.instrument_id == instrument_id)
    ]
    if not slots:
        return None
    return min(slot.plan_start for slot in slots), max(slot.plan_end for slot in slots)


def _instrument_task_spans(tasks, instrument_id: int) -> list:
    spans = []
    for task in tasks:
        if not getattr(task, "requires_instrument", False):
            continue
        span = _active_span(task, instrument_id)
        if span:
            spans.append((span[0], span[1], task))
    return sorted(spans, key=lambda item: (item[0], item[1], item[2].id))


def _manual_task_spans(tasks) -> list:
    result = []
    for task in tasks:
        if getattr(task, "requires_instrument", False):
            continue
        if not getattr(task, "requires_human", False) or not getattr(task, "assignee_id", None):
            continue
        span = _active_span(task)
        if span:
            result.append((span[0], span[1], task))
    return sorted(result, key=lambda item: (item[0], item[1], item[2].id))


def _last_before(instrument_spans, start):
    return next(
        (task for _s, end, task in reversed(instrument_spans) if end <= start),
        None,
    )


def _first_after(instrument_spans, end):
    return next(
        (task for start, _e, task in instrument_spans if start >= end),
        None,
    )


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
    # 必须和求解器同口径按 30 分钟单元取整：桥接工时会并进缺口分析的 required_hours，
    # 与已量化的 task_duration_hours 相加，混用浮点小时会让缺口偏小。
    return sum(task_duration_hours(tasks_by_id[task_id]) for task_id in task_ids)


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
