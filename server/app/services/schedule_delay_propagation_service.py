from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models import Project, Task, TaskDependency, TimeSlot
from app.services.instrument_status_service import delete_time_slots_and_refresh
from app.services.schedule_advance_notification_service import (
    capture_task_schedule_windows,
    notify_rescheduled_tasks_delayed,
)
from app.services.schedule_delay_service import (
    ScheduleDelayInvalidError,
    _advance_working_minutes,
    _ensure_within_project_end,
    _group_slot_snapshots,
    _load_working_options,
)
from app.services.schedule_forward_slot_service import build_forward_slots
from app.services.resource_replan_service import replan_resource_closure
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations
from app.services.schedule_queue_replan_support import cross_project_setup_minutes
from app.services.task_delay_status_service import reset_task_delay


MOVABLE_SLOT_STATUSES = ["scheduled", "blocked"]
MOVABLE_TASK_STATUSES = ["pending", "scheduled", "blocked"]
_logger = logging.getLogger(__name__)


def propagate_actual_delay(
    db,
    task: Task,
    planned_end: datetime,
    actual_end: datetime,
) -> dict:
    if actual_end <= planned_end:
        return {"shifted_slots": 0, "affected_tasks": 0}

    task_ids = _affected_task_ids(db, task, planned_end)
    slots = _movable_slots(db, task_ids, planned_end)
    if not slots:
        return {"shifted_slots": 0, "affected_tasks": 0}

    original_windows = capture_task_schedule_windows(
        db,
        {slot.task_id for slot in slots},
    )
    if _can_use_cp_sat_delay_replan(db, slots):
        result = _replan_actual_delay_with_cp_sat(
            db, task, slots, actual_end, original_windows,
        )
        if result.get("status") != "ok":
            raise ScheduleDelayInvalidError(result.get("message", "延期后的排程失败"))
        notify_rescheduled_tasks_delayed(
            db, original_windows, f"任务“{task.name}”实际完成延期",
        )
        return {
            "shifted_slots": len(slots),
            "affected_tasks": len({slot.task_id for slot in slots}),
        }

    _logger.warning(
        "schedule_delay_legacy_replan completed_task_id=%s affected_task_ids=%s reasons=%s",
        task.id,
        sorted({slot.task_id for slot in slots}),
        ",".join(_delay_replan_fallback_reasons(db, slots)),
    )
    snapshots_by_task = _group_slot_snapshots(slots)
    delete_time_slots_and_refresh(
        db,
        db.query(TimeSlot).filter(TimeSlot.id.in_({slot.id for slot in slots})),
        synchronize_session="fetch",
    )
    db.flush()

    options = _load_working_options(db, planned_end)
    delay_minutes = _rounded_delay_minutes(actual_end - planned_end)
    ordered_snapshots = sorted(
        snapshots_by_task.values(),
        key=lambda snapshots: (snapshots[0]["plan_start"], snapshots[0]["task_id"]),
    )
    for snapshots in ordered_snapshots:
        _restore_shifted_task(db, snapshots, delay_minutes, options, actual_end)

    rebuild_instrument_bridge_reservations(db)

    notify_rescheduled_tasks_delayed(
        db,
        original_windows,
        f"任务“{task.name}”实际完成延期",
    )
    return {
        "shifted_slots": len(slots),
        "affected_tasks": len(snapshots_by_task),
    }


def _can_use_cp_sat_delay_replan(db, slots: list[TimeSlot]) -> bool:
    task_ids = {slot.task_id for slot in slots}
    tasks = db.query(Task).filter(Task.id.in_(task_ids)).all()
    if len(tasks) != len(task_ids):
        return False
    project_ids = {task.project_id for task in tasks}
    if None in project_ids:
        return False
    project_count = db.query(Project.id).filter(Project.id.in_(project_ids)).count()
    return (
        project_count == len(project_ids)
        and all(
            task.status in {"pending", "ready", "scheduled"}
            and not task.execution_segments
            and (not task.requires_human or task.assignee_id is not None)
            and not any(
                slot.actual_start is not None or slot.actual_end is not None
                for slot in task.time_slots
                if slot.lifecycle_status == "active"
            )
            for task in tasks
        )
        and all(
            slot.status == "scheduled"
            and slot.tier != "frozen"
            and slot.actual_start is None
            and slot.actual_end is None
            for slot in slots
        )
    )


def _delay_replan_fallback_reasons(db, slots: list[TimeSlot]) -> list[str]:
    """Return explicit reasons why actual-delay propagation cannot use CP-SAT."""
    reasons: set[str] = set()
    task_ids = {slot.task_id for slot in slots}
    if not slots:
        reasons.add("no_movable_slots")
    if any(slot.status != "scheduled" for slot in slots):
        reasons.add("non_scheduled_slot")
    if any(slot.tier == "frozen" for slot in slots):
        reasons.add("frozen_slot")
    if any(
        slot.actual_start is not None or slot.actual_end is not None
        for slot in slots
    ):
        reasons.add("actual_execution_slot")

    tasks = db.query(Task).filter(Task.id.in_(task_ids)).all()
    if len(tasks) != len(task_ids):
        reasons.add("missing_task")
    project_ids = {task.project_id for task in tasks}
    if None in project_ids:
        reasons.add("missing_project")
    elif db.query(Project.id).filter(Project.id.in_(project_ids)).count() != len(project_ids):
        reasons.add("missing_project")
    for task in tasks:
        if task.status not in {"pending", "ready", "scheduled"}:
            reasons.add("non_rebuildable_task_status")
        if task.execution_segments:
            reasons.add("execution_history")
        if any(
            slot.actual_start is not None or slot.actual_end is not None
            for slot in task.time_slots
            if slot.lifecycle_status == "active"
        ):
            reasons.add("actual_execution_slot")
        if task.requires_human and task.assignee_id is None:
            reasons.add("missing_assignee")
    return sorted(reasons) or ["unknown"]


def _replan_actual_delay_with_cp_sat(
    db,
    completed_task: Task,
    slots: list[TimeSlot],
    actual_end: datetime,
    original_windows: dict[int, tuple[datetime, datetime]],
) -> dict:
    task_ids = {slot.task_id for slot in slots}
    tasks = db.query(Task).filter(Task.id.in_(task_ids)).all()
    durations = _remaining_minutes(slots)
    fixed_instruments = _fixed_instruments(tasks, slots)
    dependencies = _queue_dependencies(tasks, original_windows, slots)
    dependency_gaps = _queue_dependency_gaps(db, tasks, dependencies)
    return replan_resource_closure(
        db,
        task_ids,
        actual_end,
        completed_task.project_id,
        earliest_start_bounds={task_id: actual_end for task_id in task_ids},
        advance_notification_reason=f"任务“{completed_task.name}”实际完成延期",
        remaining_duration_minutes=durations,
        planning_start_at=actual_end,
        replaceable_after=actual_end,
        fixed_instrument_ids=fixed_instruments,
        additional_dependencies=dependencies,
        additional_dependency_gaps=dependency_gaps,
        emit_advance_notifications=False,
        commit=False,
    )


def _remaining_minutes(slots: list[TimeSlot]) -> dict[int, int]:
    result: dict[int, int] = {}
    for slot in slots:
        minutes = int((slot.plan_end - slot.plan_start).total_seconds() / 60)
        result[slot.task_id] = result.get(slot.task_id, 0) + minutes
    return result


def _fixed_instruments(tasks: list[Task], slots: list[TimeSlot]) -> dict[int, int]:
    task_by_id = {task.id: task for task in tasks}
    by_task: dict[int, set[int]] = {}
    for slot in slots:
        if slot.instrument_id is not None:
            by_task.setdefault(slot.task_id, set()).add(slot.instrument_id)
    return {
        task_id: next(iter(instrument_ids))
        for task_id, instrument_ids in by_task.items()
        if task_by_id[task_id].requires_instrument and len(instrument_ids) == 1
    }


def _queue_dependencies(
    tasks: list[Task],
    windows: dict[int, tuple[datetime, datetime]],
    slots: list[TimeSlot],
) -> list[tuple[int, int]]:
    instrument_by_task: dict[int, int] = {}
    for slot in slots:
        if slot.instrument_id is not None:
            instrument_by_task.setdefault(slot.task_id, slot.instrument_id)
    groups: dict[tuple[str, int], list[Task]] = {}
    for task in tasks:
        if task.id not in windows:
            continue
        if task.id in instrument_by_task:
            groups.setdefault(("instrument", instrument_by_task[task.id]), []).append(task)
        if task.requires_human and task.assignee_id is not None:
            groups.setdefault(("assignee", task.assignee_id), []).append(task)
    pairs: set[tuple[int, int]] = set()
    for group in groups.values():
        ordered = sorted(group, key=lambda item: windows[item.id][0])
        pairs.update((current.id, previous.id) for previous, current in zip(ordered, ordered[1:]))
    return sorted(pairs)


def _queue_dependency_gaps(
    db,
    tasks: list[Task],
    dependencies: list[tuple[int, int]],
) -> dict[tuple[int, int], int]:
    project_ids = {task.id: task.project_id for task in tasks}
    setup_units = cross_project_setup_minutes(db) // 30
    return {
        pair: setup_units
        for pair in dependencies
        if setup_units and project_ids[pair[0]] != project_ids[pair[1]]
    }


def _affected_task_ids(db, task: Task, cutoff: datetime) -> set[int]:
    project_task_ids = {
        task_id for task_id, in db.query(Task.id).filter(
            Task.id != task.id,
            Task.status.in_(MOVABLE_TASK_STATUSES),
            Task.project_id == task.project_id,
        ).all()
    }
    candidate_ids = set(project_task_ids)
    if task.requires_human and task.assignee_id is not None:
        candidate_ids.update(
            task_id for task_id, in db.query(Task.id).filter(
                Task.id != task.id,
                Task.status.in_(MOVABLE_TASK_STATUSES),
                Task.requires_human.is_(True),
                Task.assignee_id == task.assignee_id,
            ).all()
        )
    instrument_ids = {
        instrument_id for instrument_id, in db.query(TimeSlot.instrument_id).filter(
            TimeSlot.task_id == task.id,
            TimeSlot.instrument_id.isnot(None),
        ).distinct().all()
    }
    if instrument_ids:
        candidate_ids.update(
            task_id for task_id, in db.query(TimeSlot.task_id).filter(
                TimeSlot.instrument_id.in_(instrument_ids),
                TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
                TimeSlot.plan_start >= cutoff,
            ).distinct().all()
        )
    return _include_dependency_descendants(db, candidate_ids)


def _include_dependency_descendants(db, task_ids: set[int]) -> set[int]:
    affected = set(task_ids)
    frontier = set(task_ids)
    while frontier:
        rows = db.query(TaskDependency.task_id).join(
            Task,
            Task.id == TaskDependency.task_id,
        ).filter(
            TaskDependency.predecessor_id.in_(frontier),
            Task.status.in_(MOVABLE_TASK_STATUSES),
        ).distinct().all()
        descendants = {task_id for task_id, in rows} - affected
        affected.update(descendants)
        frontier = descendants
    return affected


def _movable_slots(db, task_ids: set[int], cutoff: datetime) -> list[TimeSlot]:
    if not task_ids:
        return []
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.status.in_(MOVABLE_SLOT_STATUSES),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.actual_start.is_(None),
    ).order_by(TimeSlot.plan_start, TimeSlot.id).all()
    slots_by_task: dict[int, list[TimeSlot]] = {}
    for slot in slots:
        slots_by_task.setdefault(slot.task_id, []).append(slot)
    movable_task_ids = {
        task_id for task_id, task_slots in slots_by_task.items()
        if all(slot.plan_start >= cutoff for slot in task_slots)
    }
    return [slot for slot in slots if slot.task_id in movable_task_ids]


def _restore_shifted_task(
    db,
    snapshots: list[dict],
    delay_minutes: int,
    options: dict,
    rescheduled_at: datetime,
) -> None:
    first_slot = snapshots[0]
    duration_minutes = sum(
        int((slot["plan_end"] - slot["plan_start"]).total_seconds() / 60)
        for slot in snapshots
    )
    shifted_task = db.query(Task).filter(Task.id == first_slot["task_id"]).first()
    shifted_start = _advance_working_minutes(
        first_slot["plan_start"],
        delay_minutes,
        options,
    )
    dependency_ready = _dependency_ready_time(db, shifted_task)
    if dependency_ready and dependency_ready > shifted_start:
        shifted_start = dependency_ready
    ranges = build_forward_slots(
        db,
        shifted_task,
        first_slot["instrument_id"],
        duration_minutes,
        shifted_start,
        options,
    )
    if not ranges:
        raise ScheduleDelayInvalidError("延期后的排程超出可规划范围")
    _logger.info(
        "schedule_delay_task_projection task_id=%s project_id=%s "
        "original_start=%s original_end=%s projected_start=%s projected_end=%s "
        "delay_minutes=%s",
        shifted_task.id, shifted_task.project_id,
        first_slot["plan_start"], first_slot["plan_end"],
        ranges[0][0], ranges[-1][1], delay_minutes,
    )
    _ensure_within_project_end(shifted_task, ranges[-1][1])
    if (
        shifted_task
        and shifted_task.status in {"pending", "scheduled"}
        and ranges[0][0] > rescheduled_at
    ):
        reset_task_delay(shifted_task)
    for start, end in ranges:
        db.add(TimeSlot(
            task_id=first_slot["task_id"],
            schedule_run_id=first_slot["schedule_run_id"],
            instrument_id=first_slot["instrument_id"],
            plan_start=start,
            plan_end=end,
            tier=first_slot["tier"],
            status=first_slot["status"],
        ))
    db.flush()


def _dependency_ready_time(db, task: Task | None) -> datetime | None:
    if task is None:
        return None
    predecessor_ids = [dependency.predecessor_id for dependency in task.predecessors]
    if not predecessor_ids:
        return None
    return db.query(TimeSlot.plan_end).filter(
        TimeSlot.task_id.in_(predecessor_ids),
        # 作废槽里常有排得很晚的旧版本，混进来会把"前驱何时就绪"算得过晚，
        # 延期传播据此把后续任务推到不必要的位置。
        TimeSlot.lifecycle_status == "active",
    ).order_by(TimeSlot.plan_end.desc()).limit(1).scalar()


def _rounded_delay_minutes(delay: timedelta) -> int:
    seconds = max(0, int(delay.total_seconds()))
    return max(30, ((seconds + 1799) // 1800) * 30)
