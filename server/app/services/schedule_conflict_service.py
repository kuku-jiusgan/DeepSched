from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import contains_eager

from app.models import Task, TimeSlot


class ScheduleConflictError(Exception):
    pass


ACTIVE_SLOT_STATUSES = ["scheduled", "running", "paused", "blocked", "interrupted"]


def find_instrument_conflicts(db, schedule_run_id: str | None = None) -> list[dict]:
    slots = db.query(TimeSlot).filter(
        TimeSlot.instrument_id.isnot(None),
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        TimeSlot.lifecycle_status == "active",
    ).order_by(TimeSlot.instrument_id, TimeSlot.plan_start, TimeSlot.id).all()
    by_instrument: dict[int, list[tuple[TimeSlot, object, object]]] = defaultdict(list)
    for slot in slots:
        effective_range = _effective_slot_range(slot)
        if effective_range:
            by_instrument[slot.instrument_id].append((slot, *effective_range))

    conflicts = []
    for instrument_id, instrument_slots in by_instrument.items():
        instrument_slots.sort(key=lambda item: (item[1], item[0].id))
        for index, (slot, start, end) in enumerate(instrument_slots):
            for previous_slot, previous_start, previous_end in instrument_slots[:index]:
                if previous_end <= start or not _is_schedulable_conflict(previous_slot, slot):
                    continue
                if not _in_run(previous_slot, slot, schedule_run_id):
                    continue
                conflicts.append({
                    "instrument_id": instrument_id,
                    "first_slot_id": previous_slot.id,
                    "second_slot_id": slot.id,
                    "first_task_id": previous_slot.task_id,
                    "second_task_id": slot.task_id,
                    "overlap_start": start.isoformat(),
                    "overlap_end": min(previous_end, end).isoformat(),
                    "instrument_name": _instrument_name(slot),
                    "first_schedule": _schedule_context(
                        previous_slot,
                        previous_start,
                        previous_end,
                    ),
                    "second_schedule": _schedule_context(slot, start, end),
                })
    return conflicts


def find_human_conflicts(db, schedule_run_id: str | None = None) -> list[dict]:
    # 已经 join 了 Task，用 contains_eager 把它一并取回来复用这个 join：否则下面
    # 每次访问 slot.task.assignee_id 都会再发一条 task 查询。
    slots = db.query(TimeSlot).join(Task).options(contains_eager(TimeSlot.task)).filter(
        Task.requires_human.is_(True),
        Task.assignee_id.isnot(None),
        TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        TimeSlot.lifecycle_status == "active",
    ).order_by(Task.assignee_id, TimeSlot.plan_start, TimeSlot.id).all()
    by_assignee: dict[int, list[tuple[TimeSlot, object, object]]] = defaultdict(list)
    for slot in slots:
        effective_range = _effective_slot_range(slot)
        if effective_range:
            by_assignee[slot.task.assignee_id].append((slot, *effective_range))

    conflicts = []
    for assignee_id, assignee_slots in by_assignee.items():
        assignee_slots.sort(key=lambda item: (item[1], item[0].id))
        for index, (slot, start, end) in enumerate(assignee_slots):
            for previous_slot, previous_start, previous_end in assignee_slots[:index]:
                if previous_end <= start or not _is_schedulable_conflict(previous_slot, slot):
                    continue
                if not _in_run(previous_slot, slot, schedule_run_id):
                    continue
                conflicts.append({
                    "assignee_id": assignee_id,
                    "first_slot_id": previous_slot.id,
                    "second_slot_id": slot.id,
                    "first_task_id": previous_slot.task_id,
                    "second_task_id": slot.task_id,
                    "overlap_start": start.isoformat(),
                    "overlap_end": min(previous_end, end).isoformat(),
                    "assignee_name": _assignee_name(slot),
                    "first_schedule": _schedule_context(
                        previous_slot,
                        previous_start,
                        previous_end,
                    ),
                    "second_schedule": _schedule_context(slot, start, end),
                })
    return conflicts


def _is_schedulable_conflict(first: TimeSlot, second: TimeSlot) -> bool:
    if first.task_id == second.task_id:
        return False
    return first.actual_start is None or second.actual_start is None


def _in_run(first: TimeSlot, second: TimeSlot, schedule_run_id: str | None) -> bool:
    if schedule_run_id is None:
        return True
    return schedule_run_id in {first.schedule_run_id, second.schedule_run_id}


def _effective_slot_range(slot: TimeSlot):
    # A completed historical segment without an actual end is not a resource
    # reservation and must not extend a completed predecessor into the future.
    if slot.status == "completed":
        if slot.actual_start is None or slot.actual_end is None:
            return None
        if slot.actual_end <= slot.actual_start:
            return None
        return slot.actual_start, slot.actual_end
    if slot.actual_start:
        if slot.plan_start > datetime.now():
            return _planned_slot_range(slot)
        effective_end = slot.actual_end or max(slot.plan_end, datetime.now())
        return slot.actual_start, effective_end
    return _planned_slot_range(slot)


def _planned_slot_range(slot: TimeSlot) -> tuple[datetime, datetime] | None:
    start, end = (
        slot.plan_start.replace(second=0, microsecond=0),
        slot.plan_end.replace(second=0, microsecond=0),
    )
    return (start, end) if end > start else None


def _schedule_context(slot: TimeSlot, start, end) -> dict:
    task = slot.task
    project = task.project if task else None
    return {
        "project_code": project.code if project else None,
        "project_name": project.name if project else None,
        "task_name": task.name if task else "未知任务",
        "start": start,
        "end": end,
    }


def _assignee_name(slot: TimeSlot) -> str:
    task = slot.task
    return task.assignee_name if task and task.assignee_name else "未命名负责人"


def _instrument_name(slot: TimeSlot) -> str:
    instrument = slot.instrument
    if not instrument:
        return "未命名仪器"
    return " ".join(part for part in [instrument.code, instrument.name] if part)


def _format_schedule(context: dict) -> str:
    project = " ".join(
        part for part in [context["project_code"], context["project_name"]] if part
    ) or "未知项目"
    return (
        f"项目【{project}】任务【{context['task_name']}】"
        f"（{_format_time(context['start'])} 至 {_format_time(context['end'])}）"
    )


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def ensure_no_instrument_conflicts(db, schedule_run_id: str | None = None) -> None:
    conflicts = find_instrument_conflicts(db, schedule_run_id)
    if not conflicts:
        return
    conflict = conflicts[0]
    raise ScheduleConflictError(
        f"仪器排程冲突：仪器【{conflict['instrument_name']}】的"
        f"{_format_schedule(conflict['first_schedule'])}与"
        f"{_format_schedule(conflict['second_schedule'])}发生重叠，"
        f"冲突时段为【{_format_time_from_iso(conflict['overlap_start'])} 至 "
        f"{_format_time_from_iso(conflict['overlap_end'])}】"
    )


def ensure_no_human_conflicts(db, schedule_run_id: str | None = None) -> None:
    conflicts = find_human_conflicts(db, schedule_run_id)
    if not conflicts:
        return
    conflict = conflicts[0]
    raise ScheduleConflictError(
        f"人员排程冲突：负责人【{conflict['assignee_name']}】的"
        f"{_format_schedule(conflict['first_schedule'])}与"
        f"{_format_schedule(conflict['second_schedule'])}发生重叠，"
        f"冲突时段为【{_format_time_from_iso(conflict['overlap_start'])} 至 "
        f"{_format_time_from_iso(conflict['overlap_end'])}】"
    )


def ensure_no_dependency_conflicts(
    db,
    dependency_pairs: list[tuple[int, int]],
    schedule_run_id: str | None = None,
    task_slots_from_run_only: bool = False,
) -> None:
    for task_id, predecessor_id in dependency_pairs:
        predecessor_slots = _task_effective_slots(db, predecessor_id)
        task_slots = _task_effective_slots(
            db,
            task_id,
            schedule_run_id if task_slots_from_run_only else None,
        )
        if not predecessor_slots or not task_slots:
            continue
        if schedule_run_id and not any(
            slot.schedule_run_id == schedule_run_id
            for slot, _, _ in [*predecessor_slots, *task_slots]
        ):
            continue
        predecessor_end = max(end for _, _, end in predecessor_slots)
        task_start = min(start for _, start, _ in task_slots)
        if predecessor_end <= task_start:
            continue
        predecessor = db.get(Task, predecessor_id)
        task = db.get(Task, task_id)
        raise ScheduleConflictError(
            f"任务前置关系冲突：任务【{task.name if task else task_id}】必须在"
            f"前置任务【{predecessor.name if predecessor else predecessor_id}】完成后开始，"
            f"但当前开始时间为【{_format_time(task_start)}】，前置任务完成时间为"
            f"【{_format_time(predecessor_end)}】"
        )


def _task_effective_slots(
    db,
    task_id: int,
    schedule_run_id: str | None = None,
) -> list[tuple[TimeSlot, datetime, datetime]]:
    result = []
    query = db.query(TimeSlot).filter(
        TimeSlot.task_id == task_id,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_([*ACTIVE_SLOT_STATUSES, "completed"]),
    )
    if schedule_run_id:
        query = query.filter(TimeSlot.schedule_run_id == schedule_run_id)
    slots = query.all()
    for slot in slots:
        effective_range = _effective_slot_range(slot)
        if effective_range:
            result.append((slot, *effective_range))
    return result


def _format_time_from_iso(value: str) -> str:
    return value.replace("T", " ")[:16]
