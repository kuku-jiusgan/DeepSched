from __future__ import annotations

import json

from app.models import Instrument, TimeSlot
from app.services.scheduler_helpers import (
    TIME_UNIT_MINUTES,
    datetime_to_units,
    to_units,
    units_to_datetime,
)


def unavailable_instrument_message(db, tasks, compatibility: dict[int, list[Instrument]]) -> str | None:
    for task in tasks:
        if not task.requires_instrument or compatibility.get(task.id):
            continue

        instrument_ids = _parse_instrument_ids(task)
        if not instrument_ids:
            return f"排程失败：任务【{task.name}】没有可用仪器。"

        instruments = (
            db.query(Instrument)
            .filter(Instrument.id.in_(instrument_ids))
            .order_by(Instrument.id)
            .all()
        )
        faulted = [instrument for instrument in instruments if instrument.status == "fault"]
        if faulted:
            names = "、".join(_instrument_label(instrument) for instrument in faulted)
            return f"排程失败：仪器【{names}】故障，任务【{task.name}】排程失败。"

        names = "、".join(_instrument_label(instrument) for instrument in instruments)
        return f"排程失败：指定仪器【{names or '未知仪器'}】当前不可用，任务【{task.name}】排程失败。"
    return None


def frozen_schedule_message(
    tasks,
    compatibility: dict[int, list[Instrument]],
    fixed_slots: list[TimeSlot],
    instrument_prefix_sums: dict[int, list[int]],
    horizon_start,
    total_units: int,
    setup_units: int,
) -> str | None:
    frozen_by_instrument: dict[int, list[TimeSlot]] = {}
    for slot in fixed_slots:
        if slot.tier == "frozen":
            frozen_by_instrument.setdefault(slot.instrument_id, []).append(slot)

    for task in tasks:
        candidates = compatibility.get(task.id, [])
        instrument_ids = _parse_instrument_ids(task)
        if (
            not task.requires_instrument
            or not instrument_ids
            or not candidates
            or not task.project
            or not task.project.start_date
            or not task.project.end_date
        ):
            continue

        window_start = max(0, datetime_to_units(task.project.start_date, horizon_start))
        window_end = min(total_units, datetime_to_units(task.project.end_date, horizon_start))
        required_units = to_units(
            (task.est_duration_hours or 4) + (task.switchover_hours or 0)
        )
        if window_end <= window_start:
            continue
        if all(
            _working_units(
                instrument_prefix_sums[instrument.id],
                window_start,
                window_end,
            ) < required_units
            for instrument in candidates
        ):
            continue

        blocked_instruments = []
        for instrument in candidates:
            working_prefix_sum = instrument_prefix_sums[instrument.id]
            frozen_slots = frozen_by_instrument.get(instrument.id, [])
            gaps = _available_gaps(
                task,
                frozen_slots,
                horizon_start,
                window_start,
                window_end,
                setup_units,
            )
            available_units = [
                _working_units(working_prefix_sum, start, end)
                for start, end in gaps
            ]
            has_capacity = (
                sum(available_units) >= required_units
                if task.allow_split
                else any(units >= required_units for units in available_units)
            )
            if has_capacity:
                break
            if frozen_slots:
                blocked_instruments.append(instrument)
        else:
            if len(blocked_instruments) == len(candidates):
                names = "、".join(_instrument_label(item) for item in blocked_instruments)
                required_hours = required_units * TIME_UNIT_MINUTES / 60
                return (
                    f"排程失败：项目【{task.project.name}】时间窗"
                    f"（{_format_datetime(task.project.start_date)} 至 "
                    f"{_format_datetime(task.project.end_date)}）内，冻结期内指定仪器"
                    f"【{names}】日程已满，任务【{task.name}】需要约 {required_hours:g} 小时，"
                    "无法完成排程。请延长项目日期或调整冻结排程后重试。"
                )
    return None


def schedule_infeasibility_message(
    tasks,
    task_dependencies: list[tuple[int, int]],
    missing_predecessor_ends: dict[int, int],
    compatibility: dict[int, list[Instrument]],
    global_prefix_sum: list[int],
    instrument_prefix_sums: dict[int, list[int]],
    horizon_start,
    total_units: int,
) -> str:
    predecessor_ids_by_task: dict[int, list[int]] = {}
    for task_id, predecessor_id in task_dependencies:
        if predecessor_id in missing_predecessor_ends:
            predecessor_ids_by_task.setdefault(task_id, []).append(predecessor_id)

    for task in tasks:
        project = task.project
        if not project or not project.start_date or not project.end_date:
            continue
        window_start = max(0, datetime_to_units(project.start_date, horizon_start))
        window_end = min(total_units, datetime_to_units(project.end_date, horizon_start))
        fixed_predecessor_ends = [
            missing_predecessor_ends[predecessor_id]
            for predecessor_id in predecessor_ids_by_task.get(task.id, [])
        ]
        earliest_start = max([window_start, *fixed_predecessor_ends])
        required_units = to_units(
            (task.est_duration_hours or 4) + (task.switchover_hours or 0)
        )
        prefix_sums = _task_prefix_sums(
            task,
            compatibility,
            global_prefix_sum,
            instrument_prefix_sums,
        )
        available_units = max(
            (
                _working_units(prefix_sum, earliest_start, window_end)
                for prefix_sum in prefix_sums
            ),
            default=0,
        )
        if available_units >= required_units:
            continue

        project_tasks = [
            item for item in tasks
            if item.project_id == task.project_id
        ]
        required_hours = sum(
            (item.est_duration_hours or 4) + (item.switchover_hours or 0)
            for item in project_tasks
        )
        required_units = to_units(required_hours)
        available_hours = available_units * TIME_UNIT_MINUTES / 60
        earliest_time = units_to_datetime(earliest_start, horizon_start)
        return (
            f"排程失败：项目【{project.name}】整体任务无法在项目时间窗内完成。"
            f"触发任务【{task.name}】；项目时间："
            f"{_format_datetime(project.start_date)} 至 {_format_datetime(project.end_date)}；"
            f"最早可开始时间：{_format_datetime(earliest_time)}；"
            f"任务需要约 {required_hours:g} 小时，剩余有效工时约 {available_hours:g} 小时；"
            f"项目累计任务工时：{required_hours:g} 小时。"
            "请延长项目结束时间或缩短任务工时。"
        )

    return _project_summary_message(
        tasks, compatibility, global_prefix_sum, instrument_prefix_sums,
        horizon_start, total_units,
    )


def _task_prefix_sums(
    task,
    compatibility: dict[int, list[Instrument]],
    global_prefix_sum: list[int],
    instrument_prefix_sums: dict[int, list[int]],
) -> list[list[int]]:
    if not task.requires_instrument:
        return [global_prefix_sum]
    return [
        instrument_prefix_sums[instrument.id]
        for instrument in compatibility.get(task.id, [])
        if instrument.id in instrument_prefix_sums
    ]


def _project_summary_message(
    tasks,
    compatibility: dict[int, list[Instrument]],
    global_prefix_sum: list[int],
    instrument_prefix_sums: dict[int, list[int]],
    horizon_start,
    total_units: int,
) -> str:
    tasks_by_project = {}
    for task in tasks:
        if task.project:
            tasks_by_project.setdefault(task.project_id, []).append(task)

    conflict_details = []
    project_details = []
    for project_tasks in tasks_by_project.values():
        project = project_tasks[0].project
        total_hours = sum(task.est_duration_hours or 4 for task in project_tasks)
        instrument_hours = sum(
            task.est_duration_hours or 4
            for task in project_tasks
            if task.requires_instrument
        )
        project_details.append(
            f"【{project.name}】项目时间：{_format_datetime(project.start_date)} 至 "
            f"{_format_datetime(project.end_date)}，待排总工时约 {total_hours:g} 小时"
            f"（其中仪器工时 {instrument_hours:g} 小时）"
        )
        window_start = max(0, datetime_to_units(project.start_date, horizon_start))
        window_end = min(total_units, datetime_to_units(project.end_date, horizon_start))
        available_hours = _working_units(global_prefix_sum, window_start, window_end) * TIME_UNIT_MINUTES / 60
        conflict_details.extend(_assignee_capacity_conflicts(project, project_tasks, available_hours))
        conflict_details.extend(_instrument_capacity_conflicts(
            project, project_tasks, compatibility, instrument_prefix_sums,
            window_start, window_end,
        ))

    if conflict_details:
        return "排程失败：" + "；".join(conflict_details) + "。请延长项目时间、缩短任务工时，或更换/增加负责人和仪器。"

    details = "；".join(project_details)
    return (
        f"排程失败：未找到同时满足全部约束的排程方案。"
        f"{details}。"
        f"调整后请重新点击“保存并开始排程”。"
    )


def _assignee_capacity_conflicts(project, tasks, available_hours: float) -> list[str]:
    tasks_by_assignee: dict[int, list] = {}
    for task in tasks:
        assignee_id = getattr(task, "assignee_id", None)
        if assignee_id:
            tasks_by_assignee.setdefault(assignee_id, []).append(task)
    details = []
    for assignee_tasks in tasks_by_assignee.values():
        required_hours = sum(_task_hours(task) for task in assignee_tasks)
        if required_hours <= available_hours:
            continue
        assignee_name = getattr(assignee_tasks[0], "assignee_name", None) or f"ID {assignee_tasks[0].assignee_id}"
        details.append(
            f"项目【{_project_label(project)}】的负责人【{assignee_name}】在项目时间窗内最多可排 {available_hours:g} 小时，"
            f"但其任务合计 {required_hours:g} 小时：{_task_hour_list(assignee_tasks)}"
        )
    return details


def _instrument_capacity_conflicts(
    project, tasks, compatibility, instrument_prefix_sums,
    window_start: int, window_end: int,
) -> list[str]:
    tasks_by_instrument: dict[int, list] = {}
    instruments: dict[int, Instrument] = {}
    for task in tasks:
        candidates = compatibility.get(task.id, []) if task.requires_instrument else []
        if len(candidates) != 1:
            continue
        instrument = candidates[0]
        instruments[instrument.id] = instrument
        tasks_by_instrument.setdefault(instrument.id, []).append(task)
    details = []
    for instrument_id, instrument_tasks in tasks_by_instrument.items():
        prefix_sum = instrument_prefix_sums.get(instrument_id)
        if not prefix_sum:
            continue
        available_hours = _working_units(prefix_sum, window_start, window_end) * TIME_UNIT_MINUTES / 60
        required_hours = sum(_task_hours(task) for task in instrument_tasks)
        if required_hours <= available_hours:
            continue
        details.append(
            f"项目【{_project_label(project)}】的仪器【{_instrument_label(instrument)}】在项目时间窗内最多可排 {available_hours:g} 小时，"
            f"但指定该仪器的任务合计 {required_hours:g} 小时：{_task_hour_list(instrument_tasks)}"
        )
    return details


def _task_hours(task) -> float:
    return float(task.est_duration_hours or 4) + float(task.switchover_hours or 0)


def _task_hour_list(tasks) -> str:
    return "、".join(f"【{_task_display(task)} {_task_hours(task):g}小时】" for task in tasks)


def _task_display(task) -> str:
    parent = getattr(task, "parent", None)
    return f"{parent.name}/{task.name}" if parent else task.name


def _project_label(project) -> str:
    code = getattr(project, "code", None)
    return f"{code} · {project.name}" if code and code != project.name else project.name


def _format_datetime(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "未设置"


def _available_gaps(
    task,
    frozen_slots: list[TimeSlot],
    horizon_start,
    window_start: int,
    window_end: int,
    setup_units: int,
) -> list[tuple[int, int]]:
    blocked_ranges = []
    for slot in frozen_slots:
        slot_start = datetime_to_units(slot.plan_start, horizon_start)
        slot_end = datetime_to_units(slot.plan_end, horizon_start)
        if slot_end <= window_start or slot_start >= window_end:
            continue
        padding = setup_units if slot.task and slot.task.project_id != task.project_id else 0
        blocked_ranges.append((
            max(window_start, slot_start - padding),
            min(window_end, slot_end + padding),
        ))

    cursor = window_start
    gaps = []
    for blocked_start, blocked_end in sorted(blocked_ranges):
        if blocked_start > cursor:
            gaps.append((cursor, blocked_start))
        cursor = max(cursor, blocked_end)
    if cursor < window_end:
        gaps.append((cursor, window_end))
    return gaps


def _working_units(prefix_sum: list[int], start: int, end: int) -> int:
    safe_start = max(0, min(start, len(prefix_sum) - 1))
    safe_end = max(safe_start, min(end, len(prefix_sum) - 1))
    return prefix_sum[safe_end] - prefix_sum[safe_start]

def _instrument_label(instrument: Instrument) -> str:
    return f"{instrument.name}({instrument.code})"


def _parse_instrument_ids(task) -> list[int]:
    raw_ids = getattr(task, "instrument_ids", None)
    if not raw_ids:
        return []
    if isinstance(raw_ids, str):
        try:
            raw_ids = json.loads(raw_ids)
        except (json.JSONDecodeError, ValueError):
            raw_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    if isinstance(raw_ids, list):
        return [int(instrument_id) for instrument_id in raw_ids]
    return [int(raw_ids)]
