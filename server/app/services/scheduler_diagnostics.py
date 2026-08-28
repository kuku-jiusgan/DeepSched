from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models import Instrument, TimeSlot
from app.services.scheduler_helpers import (
    TIME_UNIT_MINUTES,
    datetime_to_units,
    _parse_instrument_ids,
    task_duration_hours,
    to_units,
    units_to_datetime,
)
from app.services.scheduler_failure_diagnostics import (
    _project_instrument_intervals,
    _remaining_task_hours,
    build_project_failure_diagnostic,
)

_logger = logging.getLogger(__name__)


def log_solver_failure_snapshot(
    tasks,
    compatibility,
    task_dependencies,
    missing_predecessor_ends,
    fixed_slots,
    instrument_prefix_sums,
    horizon_start,
    total_units,
    solver_status,
) -> None:
    """Log solver inputs needed to identify deterministic infeasibility causes."""
    task_snapshot = []
    for task in tasks:
        project = getattr(task, "project", None)
        switch_hours = getattr(task, "switchover_hours", 0) or 0
        task_snapshot.append({
            "task_id": task.id,
            "project_id": task.project_id,
            "status": task.status,
            "duration_hours": getattr(task, "est_duration_hours", None),
            "switch_hours": switch_hours,
            "discrete_hours": task_duration_hours(task),
            "allow_split": bool(getattr(task, "allow_split", False)),
            "requires_human": bool(getattr(task, "requires_human", False)),
            "assignee_id": getattr(task, "assignee_id", None),
            "project_start": _format_datetime(getattr(project, "start_date", None)),
            "project_end": _format_datetime(getattr(project, "end_date", None)),
            "candidate_instrument_ids": [item.id for item in compatibility.get(task.id, [])],
        })
    fixed_snapshot = [
        {
            "slot_id": slot.id,
            "task_id": slot.task_id,
            "instrument_id": slot.instrument_id,
            "status": slot.status,
            "tier": slot.tier,
            "plan_start": _format_datetime(slot.plan_start),
            "plan_end": _format_datetime(slot.plan_end),
            "protected": slot.tier == "frozen" or slot.actual_start is not None,
        }
        for slot in fixed_slots
    ]
    _logger.error(
        "scheduler_infeasible_snapshot status=%s horizon=(%s,%s) total_units=%s "
        "tasks=%s dependencies=%s missing_predecessor_ends=%s fixed_slots=%s "
        "instrument_prefix_lengths=%s",
        solver_status,
        _format_datetime(horizon_start),
        _format_datetime(horizon_start + timedelta(minutes=total_units * TIME_UNIT_MINUTES)),
        total_units,
        task_snapshot,
        task_dependencies,
        missing_predecessor_ends,
        fixed_snapshot,
        {instrument_id: len(prefix) for instrument_id, prefix in instrument_prefix_sums.items()},
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
    current_project_id: int | None = None,
) -> str:
    if current_project_id is None:
        return _legacy_infeasibility_message(
            tasks, task_dependencies, missing_predecessor_ends, compatibility,
            global_prefix_sum, instrument_prefix_sums, horizon_start, total_units,
        )
    return schedule_infeasibility_diagnostic(
        tasks, task_dependencies, missing_predecessor_ends, compatibility,
        global_prefix_sum, instrument_prefix_sums, horizon_start, total_units,
        current_project_id,
    )["message"]


def _legacy_infeasibility_message(
    tasks, task_dependencies, missing_predecessor_ends, compatibility,
    global_prefix_sum, instrument_prefix_sums, horizon_start, total_units,
) -> str:
    predecessor_ids_by_task: dict[int, list[int]] = {}
    for task_id, predecessor_id in task_dependencies:
        if predecessor_id in missing_predecessor_ends:
            predecessor_ids_by_task.setdefault(task_id, []).append(predecessor_id)
    for task in tasks:
        project = task.project
        window_start = max(0, datetime_to_units(project.start_date, horizon_start))
        window_end = min(total_units, datetime_to_units(project.end_date, horizon_start))
        predecessor_ends = [
            missing_predecessor_ends[item]
            for item in predecessor_ids_by_task.get(task.id, [])
        ]
        earliest_start = max([window_start, *predecessor_ends])
        available_units = max((
            _working_units(prefix, earliest_start, window_end)
            for prefix in _task_prefix_sums(
                task, compatibility, global_prefix_sum, instrument_prefix_sums,
            )
        ), default=0)
        required_hours = _task_hours(task)
        if available_units < to_units(required_hours):
            return (
                f"排程失败：项目【{_project_label(project)}】任务【{task.name}】无法排入。"
                f"项目时间：{_format_datetime(project.start_date)} 至 {_format_datetime(project.end_date)}，"
                f"最早可开始时间：{_format_datetime(units_to_datetime(earliest_start, horizon_start))}，"
                f"任务需要约 {required_hours:g} 小时，剩余有效工时约 "
                f"{available_units * TIME_UNIT_MINUTES / 60:g} 小时。"
            )
    summaries = []
    for project_id in dict.fromkeys(task.project_id for task in tasks):
        project_tasks = [task for task in tasks if task.project_id == project_id]
        project = project_tasks[0].project
        window_start = max(0, datetime_to_units(project.start_date, horizon_start))
        window_end = min(total_units, datetime_to_units(project.end_date, horizon_start))
        available_hours = _working_units(
            global_prefix_sum, window_start, window_end,
        ) * TIME_UNIT_MINUTES / 60
        conflicts = _assignee_capacity_conflicts(project, project_tasks, available_hours)
        if conflicts:
            return "排程失败：" + "；".join(conflicts)
        total = sum(_remaining_task_hours(task) for task in project_tasks)
        summaries.append(
            f"【{_project_label(project)}】项目时间：{_format_datetime(project.start_date)} 至 "
            f"{_format_datetime(project.end_date)}，待排总工时约 {total:g} 小时"
        )
    return "排程失败：" + "；".join(summaries)


def schedule_infeasibility_diagnostic(
    tasks,
    task_dependencies: list[tuple[int, int]],
    missing_predecessor_ends: dict[int, int],
    compatibility: dict[int, list[Instrument]],
    global_prefix_sum: list[int],
    instrument_prefix_sums: dict[int, list[int]],
    horizon_start,
    total_units: int,
    current_project_id: int | None = None,
    excluded_task_ids: set[int] | None = None,
) -> dict:
    if current_project_id is None:
        raise ValueError("排程诊断缺少当前项目ID")
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
        return _project_summary_diagnostic(
            tasks, task_dependencies, compatibility, global_prefix_sum, instrument_prefix_sums,
            horizon_start, total_units, current_project_id, excluded_task_ids,
        )

    return _project_summary_diagnostic(
        tasks, task_dependencies, compatibility, global_prefix_sum, instrument_prefix_sums,
        horizon_start, total_units, current_project_id, excluded_task_ids,
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


def _project_summary_diagnostic(
    tasks,
    task_dependencies: list[tuple[int, int]],
    compatibility: dict[int, list[Instrument]],
    global_prefix_sum: list[int],
    instrument_prefix_sums: dict[int, list[int]],
    horizon_start,
    total_units: int,
    current_project_id: int | None = None,
    excluded_task_ids: set[int] | None = None,
) -> dict:
    return build_project_failure_diagnostic(
        tasks, compatibility, instrument_prefix_sums, horizon_start, total_units,
        current_project_id, excluded_task_ids, task_dependencies,
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
