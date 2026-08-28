from __future__ import annotations

from datetime import datetime, timedelta

from app.services.scheduler_helpers import (
    TIME_UNIT_MINUTES,
    datetime_to_units,
    task_duration_hours,
    units_to_datetime,
)
from app.services.task_progress_service import remaining_task_minutes
from app.services.scheduler_instrument_bridging import (
    bridged_instrument_hours,
    bridged_instrument_task_ids,
)
from app.services.scheduler_failure_presentation import build_failure_presentation


def build_project_failure_diagnostic(
    tasks, compatibility, instrument_prefix_sums, horizon_start, total_units,
    current_project_id: int | None, excluded_task_ids: set[int] | None,
    task_dependencies: list[tuple[int, int]],
) -> dict:
    if current_project_id is None:
        raise ValueError("排程诊断缺少当前项目ID")
    tasks_by_project = _tasks_by_project(tasks, excluded_task_ids)
    current_project = next(
        (items[0].project for items in tasks_by_project.values()
         if items[0].project_id == current_project_id),
        None,
    )
    if current_project is None:
        raise ValueError(f"排程诊断未找到当前项目ID：{current_project_id}")
    groups = _instrument_failure_groups(
        current_project, tasks_by_project, compatibility,
        instrument_prefix_sums, horizon_start, total_units, task_dependencies,
    )
    has_capacity_deficit = any(
        group["remaining_hours"] < group["required_hours"]
        for group in groups
    )
    failure = {
        "title": "排程失败",
        "kind": "instrument_capacity" if has_capacity_deficit else "scheduling_constraints",
        "summary": (
            "计划内仪器工时不足"
            if has_capacity_deficit
            else "项目未能在截止日期前排入，受连续时间段、人员、前置依赖或仪器切换等排程约束限制"
        ),
        "deadline": _format_datetime(current_project.end_date),
        "groups": groups,
        **build_failure_presentation(current_project, groups),
    }
    return {"message": format_failure_message(failure), "schedule_failure": failure}


def build_task_window_failure(task, earliest_start: datetime, duration_units: int) -> dict:
    project = task.project
    required_hours = duration_units * TIME_UNIT_MINUTES / 60
    end_date = project.end_date if project else None
    available_hours = max(
        0.0,
        (end_date - earliest_start).total_seconds() / 3600,
    ) if end_date else 0.0
    project_label = _project_label(project) if project else "未知项目"
    summary = f"项目【{project_label}】的任务【{task.name}】有效时间窗口不足"
    failure = {
        "title": "排程失败",
        "kind": "scheduling_constraints",
        "summary": summary,
        "deadline": _format_datetime(end_date),
        "groups": [],
        "project_id": project.id if project else None,
        "project_label": project_label,
        "window": {
            "task_name": task.name,
            "earliest_start": _format_datetime(earliest_start),
            "deadline": _format_datetime(end_date),
            "required_hours": required_hours,
            "available_hours": available_hours,
        },
    }
    return {"message": format_failure_message(failure), "schedule_failure": failure}


def _tasks_by_project(tasks, excluded_task_ids: set[int] | None) -> dict:
    tasks_by_project = {}
    excluded_ids = excluded_task_ids or set()
    for task in tasks:
        if not task.project:
            continue
        all_project_tasks = list(getattr(task.project, "tasks", []) or [])
        project_tasks = [
            item for item in all_project_tasks
            if item.id not in excluded_ids and not getattr(item, "children", [])
        ]
        tasks_by_project.setdefault(task.project_id, project_tasks or all_project_tasks or [task])
    return tasks_by_project


def _instrument_failure_groups(
    current_project, tasks_by_project, compatibility, instrument_prefix_sums,
    horizon_start, total_units, task_dependencies,
) -> list[dict]:
    current_tasks = tasks_by_project.get(current_project.id, [])
    instruments = {}
    group_keys = set()
    for task in current_tasks:
        root = _top_level_task(task)
        candidates = list(compatibility.get(task.id, []))
        instruments.update({item.id: item for item in candidates})
        slot_ids = {
            slot.instrument_id for slot in (getattr(task, "time_slots", []) or [])
            if slot.instrument_id
        }
        root_ids = set(getattr(root, "instrument_ids", None) or [])
        for instrument_id in ({item.id for item in candidates} | slot_ids | root_ids):
            if instrument_id in instrument_prefix_sums:
                group_keys.add((root.id, instrument_id))

    roots = {_top_level_task(task).id: _top_level_task(task) for task in current_tasks}
    today_start = max(datetime.now(), current_project.start_date or datetime.now())
    deadline = current_project.end_date
    start_unit = max(0, datetime_to_units(today_start, horizon_start))
    end_unit = min(total_units, datetime_to_units(deadline, horizon_start))
    segment_ends = sorted({
        end_unit,
        *(
            min(total_units, datetime_to_units(items[0].project.end_date, horizon_start))
            for items in tasks_by_project.values()
            if items and getattr(items[0].project, "end_date", None)
        ),
    })
    segment_ends = [item for item in segment_ends if item > start_unit]
    segment_starts = [start_unit, *segment_ends[:-1]]
    groups = []
    for root_id, instrument_id in sorted(group_keys):
        instrument = instruments.get(instrument_id)
        instrument_label = _instrument_label(instrument) if instrument else f"仪器ID {instrument_id}"
        capacities = [
            _working_units(instrument_prefix_sums[instrument_id], start, end) * TIME_UNIT_MINUTES / 60
            for start, end in zip(segment_starts, segment_ends)
        ]
        details, occupied_hours = _occupied_project_details(
            current_project.id, tasks_by_project, compatibility, instrument_id,
            instrument_label, today_start, horizon_start, end_unit, segment_ends, capacities,
            task_dependencies,
        )
        available = _working_units(
            instrument_prefix_sums[instrument_id], start_unit, end_unit,
        ) * TIME_UNIT_MINUTES / 60
        required_hours = sum(
            task_duration_hours(task)
            for task in current_tasks
            if getattr(task, "requires_instrument", False)
            and (
                instrument_id in {item.id for item in compatibility.get(task.id, [])}
                or instrument_id in set(getattr(_top_level_task(task), "instrument_ids", None) or [])
            )
        )
        required_hours += bridged_instrument_hours(
            current_tasks, task_dependencies, compatibility, instrument_id, root_id,
        )
        remaining_hours = max(0, available - occupied_hours)
        groups.append({
            "top_level_task_id": root_id,
            "top_level_task_name": roots[root_id].name,
            "instrument_id": instrument_id,
            "instrument_label": instrument_label,
            "deadline": _format_datetime(deadline),
            "available_hours": available,
            "occupied_hours": occupied_hours,
            "remaining_hours": remaining_hours,
            "required_hours": required_hours,
            "deficit_hours": max(0, required_hours - remaining_hours),
            "details": details,
        })
    return groups


def _occupied_project_details(
    current_project_id, tasks_by_project, compatibility, instrument_id, instrument_label,
    today_start, horizon_start, end_unit, segment_ends, capacities, task_dependencies=(),
) -> tuple[list[dict], float]:
    details = []
    occupied_hours = 0.0
    segment_starts = [max(0, datetime_to_units(today_start, horizon_start)), *segment_ends[:-1]]
    for project_id, project_tasks in sorted(
        tasks_by_project.items(),
        key=lambda item: getattr(item[1][0].project, "end_date", datetime.max),
        reverse=True,
    ):
        if project_id == current_project_id:
            continue
        project_deadline = getattr(project_tasks[0].project, "end_date", None)
        if not project_deadline:
            continue
        _intervals, breakdown = _project_instrument_intervals(
            project_tasks, instrument_id, compatibility, today_start, project_deadline,
            task_dependencies,
        )
        resource_intervals = breakdown["resource_intervals"]
        if not resource_intervals:
            continue
        scheduled_allocated = _allocate_scheduled_hours(
            resource_intervals, segment_starts, segment_ends, capacities, horizon_start, end_unit,
        )
        bridged_allocated = _allocate_scheduled_hours(
            resource_intervals, segment_starts, segment_ends, capacities, horizon_start, end_unit,
            kind="bridge",
        )
        forecast_allocated = _allocate_forecast_hours(
            resource_intervals, project_deadline, segment_ends, capacities, horizon_start, end_unit,
        )
        allocated = scheduled_allocated + bridged_allocated + forecast_allocated
        if allocated <= 0:
            continue
        occupied_hours += allocated
        details.append({
            "project_id": project_id,
            "project_label": _project_label(project_tasks[0].project),
            "instrument_label": instrument_label,
            "scheduled_hours": scheduled_allocated,
            "bridged_hours": bridged_allocated,
            "forecast_hours": forecast_allocated,
            "waiting_hours": breakdown["waiting"],
            "total_hours": allocated,
        })
    return details, occupied_hours


def _allocate_scheduled_hours(
    resource_intervals, starts, ends, capacities, horizon_start, current_end, kind="slot",
) -> float:
    """按时间段把已排占用摊进各段容量。kind="slot" 是仪器自身占用，"bridge" 是桥接的人工占用。"""
    allocated = 0.0
    for index, (segment_start, segment_end) in enumerate(zip(starts, ends)):
        start_time = units_to_datetime(segment_start, horizon_start)
        end_time = units_to_datetime(segment_end, horizon_start)
        hours = _interval_hours([
            (max(start, start_time), min(end, end_time))
            for start, end, interval_kind in resource_intervals
            if interval_kind == kind and end > start_time and start < end_time
        ])
        used = min(hours, capacities[index])
        capacities[index] -= used
        if segment_end <= current_end:
            allocated += used
    return allocated


def _allocate_forecast_hours(resource_intervals, project_deadline, segment_ends, capacities, horizon_start, current_end) -> float:
    remaining = _interval_hours([
        (start, end) for start, end, kind in resource_intervals if kind == "forecast"
    ])
    allocated = 0.0
    deadline_unit = datetime_to_units(project_deadline, horizon_start)
    for index in reversed([i for i, end in enumerate(segment_ends) if end <= deadline_unit]):
        used = min(remaining, capacities[index])
        capacities[index] -= used
        remaining -= used
        if segment_ends[index] <= current_end:
            allocated += used
        if remaining <= 0:
            break
    return allocated


def format_failure_message(failure: dict) -> str:
    lines = [f"{failure['title']}：{failure['summary']}（截止日期：{failure.get('deadline', '未知')}）"]
    if failure.get("kind") != "instrument_capacity":
        lines.append("请检查任务前置关系、负责人可用时间、仪器连续可用时段和切换时间后重新排程。")
        return "\n".join(lines)
    lines.extend(["------------------------------------------------------------", "⚠️ 缺口汇总："])
    for index, group in enumerate((item for item in failure["groups"] if item["deficit_hours"] > 0), 1):
        lines.append(
            f"{index}. [{group['top_level_task_name']}] {group['instrument_label']} ｜ "
            f"需求: {group['required_hours']:g}h ｜ 剩余: {group['remaining_hours']:g}h ｜ "
            f"【缺口: {group['deficit_hours']:g}h】"
        )
    lines.append("📋 现有占用明细：")
    for group in failure["groups"]:
        lines.append(f"• {group['top_level_task_name']} (已用 {group['occupied_hours']:g}h / 总 {group['available_hours']:g}h):")
        if group["details"]:
            lines.extend(
                f"  - {detail['project_label']}: {detail['total_hours']:g}h"
                for detail in group["details"]
            )
        else:
            lines.append("  - 无")
    lines.extend(["------------------------------------------------------------", "请调整项目日期或减少工时后，重新点击“保存并开始排程”。"])
    return "\n".join(lines)


def _remaining_task_hours(task) -> float:
    """任务剩余工时，口径与求解器一致。

    求解器排已开始的任务时只排剩余部分，用的是 remaining_duration_units；
    对带 executed_minutes 字段的真实任务，它扣的是累计有效执行分钟数。
    诊断这里原先改按 execution_segments 的墙钟跨度扣减，把夜间和周末也算成
    已执行工时（周五 18:00 到周一 10:00 会被记成 64 小时），于是缺口分析以为
    任务快做完了，求解器却还要排掉大半——第一层判定工时够用，第二层排不下。
    墙钟口径同时看不见延期追加的计划工时（additional_planned_minutes）。
    """
    if getattr(task, "status", None) in {"done", "completed"}:
        return 0
    return remaining_task_minutes(task) / 60


def _project_instrument_intervals(
    project_tasks, instrument_id, compatibility, window_start, window_end,
    task_dependencies=(),
):
    intervals = []
    # 夹在两个"同仪器 + 同负责人"任务之间的非仪器任务（方案撰写、报告撰写等）
    # 期间仪器不会被释放，必须算进该仪器的占用，否则缺口分析会偏乐观。
    bridge_task_ids = bridged_instrument_task_ids(
        project_tasks, task_dependencies, compatibility, instrument_id,
    )
    for task in project_tasks:
        if getattr(task, "status", None) in {"done", "completed"}:
            continue
        if task.id in bridge_task_ids:
            intervals.extend(_bridge_intervals(task, window_start, window_end))
            continue
        if not getattr(task, "requires_instrument", False):
            continue
        root = _top_level_task(task)
        root_ids = set(getattr(root, "instrument_ids", None) or [])
        compatible_ids = {item.id for item in compatibility.get(task.id, [])}
        task_slots = list(getattr(task, "time_slots", []) or [])
        has_slot = any(slot.instrument_id == instrument_id for slot in task_slots)
        if not (has_slot or instrument_id in compatible_ids or instrument_id in root_ids):
            continue
        if task_slots and all(slot.status == "completed" for slot in task_slots):
            continue
        slots = [
            slot for slot in task_slots
            if slot.instrument_id == instrument_id and slot.plan_start and slot.plan_end
            and slot.status in {"scheduled", "running", "blocked", "paused"}
        ]
        if slots:
            intervals.extend((max(window_start, slot.plan_start), min(window_end, slot.plan_end), task, "slot") for slot in slots)
            continue
        deadline = min(filter(None, (getattr(task, "latest_due", None), project_tasks[0].project.end_date)), default=None)
        if deadline:
            end = min(window_end, deadline)
            start = end - timedelta(hours=_remaining_task_hours(task))
            if end > window_start and start < window_end:
                intervals.append((max(window_start, start), end, task, "forecast"))
    intervals = [item for item in intervals if item[1] > item[0]]
    resource_intervals = [(start, end, kind) for start, end, _task, kind in intervals]
    intervals.sort(key=lambda item: item[0])
    waiting_hours = sum(
        (current[0] - previous[1]).total_seconds() / 3600
        for previous, current in zip(intervals, intervals[1:])
        if current[0] > previous[1] and previous[2].id != current[2].id
        and getattr(previous[2], "assignee_id", None) == getattr(current[2], "assignee_id", None)
        and getattr(previous[2], "assignee_id", None)
    )
    return [], {
        "slot": _interval_hours([(start, end) for start, end, _task, kind in intervals if kind == "slot"]),
        "bridge": _interval_hours([(start, end) for start, end, _task, kind in intervals if kind == "bridge"]),
        "forecast": _interval_hours([(start, end) for start, end, _task, kind in intervals if kind == "forecast"]),
        "waiting": waiting_hours,
        "resource_intervals": resource_intervals,
    }


def _bridge_intervals(task, window_start, window_end):
    """桥接任务占住仪器的时间。

    已排就用它自己的时间槽。重排会把原时间槽置为 superseded，此时任务仍然
    是"已有计划、正在被重排"，占用性质不变，继续按原计划位置计入人工占用；
    否则占用明细里的人工占用会凭空变成 0，工时被记进预测工时列。只有从未
    排过的任务才按剩余工时倒排到截止日，计入预测工时。
    """
    planned = [
        slot for slot in (getattr(task, "time_slots", []) or [])
        if slot.plan_start and slot.plan_end
    ]
    active = [
        slot for slot in planned
        if getattr(slot, "lifecycle_status", "active") == "active"
        and slot.status in {"scheduled", "running", "blocked", "paused"}
    ]
    slots = active or planned
    if slots:
        # 同一任务被反复重排会留下多份时间范围相同的作废时间槽，必须先合并，
        # 否则同一段占用会被重复累加。
        spans = _merge_spans([
            (max(window_start, slot.plan_start), min(window_end, slot.plan_end))
            for slot in slots
        ])
        kind = "bridge"
    else:
        start = window_end - timedelta(hours=_remaining_task_hours(task))
        spans = [(max(window_start, start), window_end)]
        kind = "forecast"
    return [(start, end, task, kind) for start, end in spans if end > start]


def _merge_spans(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return merged


def _top_level_task(task):
    current = task
    visited = set()
    while getattr(current, "parent", None) is not None and current.id not in visited:
        visited.add(current.id)
        current = current.parent
    return current


def _interval_hours(intervals) -> float:
    return sum((end - start).total_seconds() / 3600 for start, end in intervals)


def _working_units(prefix_sum: list[int], start: int, end: int) -> int:
    safe_start = max(0, min(start, len(prefix_sum) - 1))
    safe_end = max(safe_start, min(end, len(prefix_sum) - 1))
    return prefix_sum[safe_end] - prefix_sum[safe_start]


def _project_label(project) -> str:
    code = getattr(project, "code", None)
    return f"{code} · {project.name}" if code and code != project.name else project.name


def _instrument_label(instrument) -> str:
    return f"{instrument.name}({instrument.code})"


def _format_datetime(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "未设置"
