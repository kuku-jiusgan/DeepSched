from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models import Task, TimeSlot
from app.services.resource_replan_service import replan_resource_closure
from app.services.schedule_queue_replan_support import (
    cross_project_setup_minutes,
    is_movable_task,
    load_forward_shift_candidates,
)


_logger = logging.getLogger(__name__)


def replan_released_resource_queue(
    db,
    instrument_id: int | None,
    released_at: datetime,
    assignee_id: int | None = None,
    previous_project_id: int | None = None,
) -> dict:
    """Replan only movable work after an early completion releases capacity."""
    all_candidates = load_forward_shift_candidates(
        db, instrument_id, released_at, assignee_id,
    )
    candidates = []
    for candidate in all_candidates:
        if not is_movable_task(db, candidate, instrument_id, released_at, assignee_id):
            # Only a contiguous movable queue prefix may advance. Skipping a
            # fixed task would let later work jump ahead of its queue position.
            break
        candidates.append(candidate)
    _logger.info(
        "early_completion_replan_candidates instrument_id=%s released_at=%s assignee_id=%s candidate_task_ids=%s",
        instrument_id, released_at, assignee_id, [task.id for task in candidates],
    )
    if not candidates:
        return _empty_result(instrument_id)
    original_windows = _scheduled_windows(db, candidates)
    remaining_minutes = _scheduled_minutes(db, candidates)
    fixed_instruments = _fixed_instruments(db, candidates)
    queue_instruments = _queue_instruments(db, candidates)
    queue_dependencies = _queue_dependencies(candidates, original_windows, queue_instruments)
    queue_gaps = _queue_dependency_gaps(db, candidates, queue_dependencies)
    allowed_unassigned = {
        task.id for task in candidates if task.requires_human and not task.assignee_id
    }
    earliest_start_bounds = _first_task_start_bound(
        db, candidates, released_at, previous_project_id,
    )
    result = replan_resource_closure(
        db,
        {task.id for task in candidates},
        released_at,
        # 当前项目取队首候选所属的项目，而不是刚完成任务的项目。这次重排排的是
        # 被释放资源上可以前移的**其他**任务，刚完成任务的项目一个任务都不在求解
        # 集合里；拿它当 current_project_id 会让工时校验、失败诊断和交期建议
        # 全部指向一个没在排的项目——王方就曾看到"项目未能在截止日期前排入"
        # 报的是另一个项目的结题日。previous_project_id 仍用于判断队首任务是否
        # 跨项目、要不要加切换时间，那个用法是对的。
        current_project_id=candidates[0].project_id,
        earliest_start_bounds=earliest_start_bounds,
        advance_notification_reason="任务提前完成后资源释放",
        remaining_duration_minutes=remaining_minutes,
        planning_start_at=released_at,
        replaceable_after=released_at,
        fixed_instrument_ids=fixed_instruments,
        allow_unassigned_human_task_ids=allowed_unassigned,
        additional_dependencies=queue_dependencies,
        additional_dependency_gaps=queue_gaps,
        emit_advance_notifications=False,
        commit=False,
    )
    _logger.info(
        "early_completion_replan_result instrument_id=%s released_at=%s candidate_task_ids=%s status=%s message=%s",
        instrument_id, released_at, [task.id for task in candidates], result.get("status"), result.get("message"),
    )
    if result.get("status") != "ok":
        return {**result, "moved_tasks": 0, "moved_task_details": []}
    details = _window_changes(db, original_windows)
    return {
        "status": "ok",
        "message": (
            f"任务已完成，按责任人前移 {len(details)} 个任务"
            if instrument_id is None
            else f"任务已完成，该仪器跨项目前移 {len(details)} 个任务"
        ),
        "moved_tasks": len(details),
        "moved_task_details": details,
        "schedule_run_id": result.get("schedule_run_id"),
    }


def _empty_result(instrument_id: int | None) -> dict:
    message = "任务已完成，无后续任务可前移"
    if instrument_id is not None:
        message = "任务已完成，该仪器无后续任务可前移"
    return {"status": "ok", "message": message, "moved_tasks": 0}


def _scheduled_windows(db, tasks: list[Task]) -> dict[int, tuple[datetime, datetime]]:
    return {
        task.id: window
        for task in tasks
        if (window := _task_scheduled_window(db, task.id)) is not None
    }


def _scheduled_minutes(db, tasks: list[Task]) -> dict[int, int]:
    result = {}
    for task in tasks:
        slots = _movable_slots(db, task.id)
        if slots:
            result[task.id] = sum(
                int((slot.plan_end - slot.plan_start).total_seconds() / 60)
                for slot in slots
            )
    return result


def _fixed_instruments(db, tasks: list[Task]) -> dict[int, int]:
    result = {}
    for task in tasks:
        if not task.requires_instrument:
            continue
        instrument_ids = {
            slot.instrument_id for slot in _movable_slots(db, task.id)
            if slot.instrument_id is not None
        }
        if len(instrument_ids) == 1:
            result[task.id] = instrument_ids.pop()
    return result


def _queue_dependencies(
    candidates: list[Task],
    original_windows: dict[int, tuple[datetime, datetime]],
    queue_instruments: dict[int, int],
) -> list[tuple[int, int]]:
    """Preserve the original order on every affected instrument and person queue."""
    groups: dict[tuple[str, int], list[Task]] = {}
    for task in candidates:
        if task.id not in original_windows:
            continue
        instrument_id = queue_instruments.get(task.id)
        if instrument_id is not None:
            groups.setdefault(("instrument", instrument_id), []).append(task)
        if task.requires_human and task.assignee_id is not None:
            groups.setdefault(("assignee", task.assignee_id), []).append(task)
    dependencies: set[tuple[int, int]] = set()
    for tasks in groups.values():
        ordered = sorted(tasks, key=lambda task: original_windows[task.id][0])
        dependencies.update(
            (current.id, previous.id) for previous, current in zip(ordered, ordered[1:])
        )
    return sorted(dependencies)


def _queue_instruments(db, tasks: list[Task]) -> dict[int, int]:
    result = {}
    for task in tasks:
        instrument_ids = {
            slot.instrument_id for slot in _movable_slots(db, task.id)
            if slot.instrument_id is not None
        }
        if len(instrument_ids) == 1:
            result[task.id] = instrument_ids.pop()
    return result


def _queue_dependency_gaps(
    db,
    candidates: list[Task],
    dependencies: list[tuple[int, int]],
) -> dict[tuple[int, int], int]:
    projects = {task.id: task.project_id for task in candidates}
    setup_units = cross_project_setup_minutes(db) // 30
    return {
        pair: setup_units
        for pair in dependencies
        if setup_units and projects[pair[0]] != projects[pair[1]]
    }


def _first_task_start_bound(
    db,
    candidates: list[Task],
    released_at: datetime,
    previous_project_id: int | None,
) -> dict[int, datetime]:
    first = candidates[0]
    if previous_project_id is None or first.project_id == previous_project_id:
        return {first.id: released_at}
    setup_minutes = cross_project_setup_minutes(db)
    return {first.id: released_at + timedelta(minutes=setup_minutes)}


def _window_changes(
    db,
    original_windows: dict[int, tuple[datetime, datetime]],
) -> list[dict]:
    details = []
    for task_id, original_window in original_windows.items():
        new_window = _task_scheduled_window(db, task_id)
        if new_window is None or new_window[0] >= original_window[0]:
            continue
        details.append({
            "task_id": task_id,
            "original_start": original_window[0],
            "original_end": original_window[1],
            "new_start": new_window[0],
            "new_end": new_window[1],
        })
    return details


def _task_scheduled_window(db, task_id: int) -> tuple[datetime, datetime] | None:
    slots = _movable_slots(db, task_id)
    if not slots:
        return None
    return slots[0].plan_start, slots[-1].plan_end


def _movable_slots(db, task_id: int) -> list[TimeSlot]:
    return db.query(TimeSlot).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status == "scheduled",
        TimeSlot.actual_start.is_(None),
        TimeSlot.actual_end.is_(None),
    ).order_by(TimeSlot.plan_start, TimeSlot.id).all()
