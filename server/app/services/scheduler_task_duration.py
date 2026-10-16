"""任务剩余时长推算：把已执行的工时从计划时长里扣掉。

已开始的任务重排时只应排剩余部分，扣减口径按有效工作时间（prefix sum）
计算，因此夜间和周末不计入已执行工时。
"""

from __future__ import annotations

from datetime import datetime

from app.services.scheduler_helpers import datetime_to_units, switchover_units, to_units


def remaining_duration_units(
    task,
    duration_units: int,
    fixed_slots,
    global_prefix_sum,
    instrument_prefix_sums,
    horizon_start,
    total_units: int,
    remaining_duration_minutes: dict[int, int] | None = None,
    now: datetime | None = None,
) -> int:
    if remaining_duration_minutes and task.id in remaining_duration_minutes:
        return remaining_workload_units(task, remaining_duration_minutes)
    if hasattr(task, "executed_minutes"):
        return remaining_workload_units(task)
    segments = list(getattr(task, "execution_segments", []) or [])
    fixed_units = executed_duration_units(
        segments,
        task,
        global_prefix_sum,
        instrument_prefix_sums,
        horizon_start,
        total_units,
        now,
    )
    if not segments:
        fixed_units = executed_slot_duration_units(
            task,
            fixed_slots,
            global_prefix_sum,
            instrument_prefix_sums,
            horizon_start,
            total_units,
            now,
        )
    return max(1, duration_units - fixed_units)


def remaining_workload_units(
    task,
    remaining_duration_minutes: dict[int, int] | None = None,
) -> int:
    """求解器和窗口估算共用的剩余工作量，不依赖模型窗口或固定槽。

    显式剩余工时按调用方给定值建模，不额外追加任务准备工时。
    其余任务使用计划工时、追加工时、已执行工时及任务准备工时。
    各部分保持求解器的 30 分钟取整规则。
    """
    from app.services.task_progress_service import remaining_task_minutes

    if remaining_duration_minutes and task.id in remaining_duration_minutes:
        return max(1, to_units(remaining_duration_minutes[task.id] / 60))
    remaining_minutes = remaining_task_minutes(task)
    remaining_units = to_units(remaining_minutes / 60) if remaining_minutes > 0 else 0
    return max(1, remaining_units + switchover_units(task))


def executed_duration_units(
    segments,
    task,
    global_prefix_sum,
    instrument_prefix_sums,
    horizon_start,
    total_units,
    now: datetime | None = None,
) -> int:
    total = 0
    instrument_id = next(
        (getattr(segment, "instrument_id", None) for segment in segments
         if getattr(segment, "instrument_id", None) is not None),
        None,
    )
    prefix_sum = instrument_prefix_sums.get(instrument_id) if instrument_id else global_prefix_sum
    if not prefix_sum:
        return 0
    for segment in segments:
        start = max(0, datetime_to_units(segment.started_at, horizon_start))
        end_time = segment.ended_at or now or datetime.now()
        end = min(total_units, datetime_to_units(end_time, horizon_start))
        if end > start:
            total += prefix_sum[end] - prefix_sum[start]
    return total


def executed_slot_duration_units(
    task,
    fixed_slots,
    global_prefix_sum,
    instrument_prefix_sums,
    horizon_start,
    total_units,
    now: datetime | None = None,
) -> int:
    total = 0
    for slot in fixed_slots:
        if slot.task_id != task.id or not slot.actual_start:
            continue
        start = max(0, datetime_to_units(slot.actual_start, horizon_start))
        end_time = slot.actual_end or now or datetime.now()
        end = min(total_units, datetime_to_units(end_time, horizon_start))
        prefix_sum = (
            instrument_prefix_sums.get(slot.instrument_id)
            if slot.instrument_id is not None else global_prefix_sum
        )
        if prefix_sum and end > start:
            total += prefix_sum[end] - prefix_sum[start]
    return total
