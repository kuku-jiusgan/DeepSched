from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ortools.sat.python import cp_model

from app.models import TimeSlot
from app.services.scheduler_helpers import datetime_to_units


FIXED_SLOT_STATUSES = ["scheduled", "running", "completed", "paused", "blocked", "interrupted"]


def _fixed_slot_range(slot: TimeSlot) -> tuple[datetime, datetime]:
    if slot.status == "completed":
        return slot.actual_start, slot.actual_end
    if slot.actual_start:
        if slot.plan_start > datetime.now():
            return slot.plan_start, slot.plan_end
        return slot.actual_start, slot.actual_end or max(slot.plan_end, datetime.now())
    return slot.plan_start, slot.plan_end


def _merge_task_ranges(
    ranges: list[tuple[TimeSlot, int, int]],
) -> list[tuple[TimeSlot, int, int]]:
    merged: list[tuple[TimeSlot, int, int]] = []
    for slot, start, end in sorted(ranges, key=lambda item: (item[0].task_id, item[1])):
        if merged and merged[-1][0].task_id == slot.task_id and start < merged[-1][2]:
            previous_slot, previous_start, previous_end = merged[-1]
            merged[-1] = (previous_slot, previous_start, max(previous_end, end))
            continue
        merged.append((slot, start, end))
    return merged


def _is_protected_slot(slot: TimeSlot) -> bool:
    return (
        slot.tier == "frozen"
        or slot.status == "running"
        or (slot.actual_start is not None and slot.actual_end is None)
    )


def load_fixed_slots(
    db,
    excluded_task_ids: set[int] | None = None,
    relevant_instrument_ids: set[int] | None = None,
    relevant_assignee_ids: set[int] | None = None,
) -> list[TimeSlot]:
    query = db.query(TimeSlot).filter(
        TimeSlot.status.in_(FIXED_SLOT_STATUSES),
    )
    slots = query.order_by(TimeSlot.instrument_id, TimeSlot.plan_start, TimeSlot.id).all()
    fixed_slots = [
        slot for slot in slots
        if slot.status != "completed" or (slot.actual_start and slot.actual_end)
    ]
    if excluded_task_ids:
        fixed_slots = [
            slot for slot in fixed_slots
            if slot.task_id not in excluded_task_ids or _is_protected_slot(slot)
        ]
    if relevant_instrument_ids is None and relevant_assignee_ids is None:
        return fixed_slots

    instrument_ids = relevant_instrument_ids or set()
    assignee_ids = relevant_assignee_ids or set()
    return [
        slot for slot in fixed_slots
        if slot.instrument_id in instrument_ids
        or (
            slot.task
            and slot.task.requires_human
            and slot.task.assignee_id in assignee_ids
        )
    ]


def add_human_capacity_constraints(
    model: cp_model.CpModel,
    tasks,
    task_intervals: dict[int, cp_model.IntervalVar],
    fixed_slots: list[TimeSlot],
    horizon_start,
    total_units: int,
) -> None:
    intervals_by_assignee: dict[int, list[cp_model.IntervalVar]] = defaultdict(list)
    fixed_by_assignee: dict[int, list[tuple[TimeSlot, int, int]]] = defaultdict(list)
    for task in tasks:
        if task.requires_human and task.assignee_id:
            intervals_by_assignee[task.assignee_id].append(task_intervals[task.id])

    for slot in fixed_slots:
        task = slot.task
        if not task or not task.requires_human or not task.assignee_id:
            continue
        start_time, end_time = _fixed_slot_range(slot)
        start_unit = datetime_to_units(start_time, horizon_start)
        end_unit = datetime_to_units(end_time, horizon_start)
        if end_unit <= 0 or start_unit >= total_units:
            continue
        clipped_start = max(0, start_unit)
        clipped_end = min(total_units, end_unit)
        fixed_by_assignee[task.assignee_id].append((slot, clipped_start, clipped_end))

    for assignee_id, ranges in fixed_by_assignee.items():
        for slot, start_unit, end_unit in _merge_task_ranges(ranges):
            intervals_by_assignee[assignee_id].append(model.NewIntervalVar(
                start_unit,
                end_unit - start_unit,
                end_unit,
                f"fixed_human_slot_{slot.id}",
            ))

    for assignee_intervals in intervals_by_assignee.values():
        if assignee_intervals:
            model.AddNoOverlap(assignee_intervals)


def add_instrument_capacity_constraints(
    model: cp_model.CpModel,
    instruments,
    tasks,
    capacity_intervals,
    presences,
    inst_starts,
    inst_ends,
    split_unit_presences,
    fixed_slots: list[TimeSlot],
    horizon_start,
    total_units: int,
    non_overlap_enabled: bool,
    setup_units: int,
) -> None:
    fixed_by_instrument: dict[int, list[tuple[TimeSlot, int, int]]] = defaultdict(list)
    for slot in fixed_slots:
        if slot.instrument_id is None:
            continue
        start_time, end_time = _fixed_slot_range(slot)
        start_unit = datetime_to_units(start_time, horizon_start)
        end_unit = datetime_to_units(end_time, horizon_start)
        if end_unit <= 0 or start_unit >= total_units:
            continue
        fixed_by_instrument[slot.instrument_id].append(
            (slot, max(0, start_unit), min(total_units, end_unit))
        )
    fixed_by_instrument = {
        instrument_id: _merge_task_ranges(ranges)
        for instrument_id, ranges in fixed_by_instrument.items()
    }

    task_by_id = {task.id: task for task in tasks}
    for instrument in instruments:
        instrument_intervals = list(capacity_intervals.get(instrument.id, []))
        for slot, start_unit, end_unit in fixed_by_instrument.get(instrument.id, []):
            instrument_intervals.append(model.NewIntervalVar(
                start_unit,
                end_unit - start_unit,
                end_unit,
                f"fixed_slot_{slot.id}",
            ))

        if instrument_intervals and non_overlap_enabled:
            model.AddNoOverlap(instrument_intervals)

        protected_ends = [
            end_unit
            for slot, _, end_unit in fixed_by_instrument.get(instrument.id, [])
            if _is_protected_slot(slot)
        ]
        if protected_ends:
            protected_queue_end = max(protected_ends)
            for key, presence in presences.items():
                task_id, instrument_id = key
                if instrument_id != instrument.id:
                    continue
                task = task_by_id[task_id]
                if task.allow_split:
                    for split_key, unit_presence in split_unit_presences.items():
                        split_task_id, split_instrument_id, unit = split_key
                        if (
                            split_task_id == task_id
                            and split_instrument_id == instrument.id
                            and unit < protected_queue_end
                        ):
                            model.Add(unit_presence == 0)
                    continue
                model.Add(
                    inst_starts[key] >= protected_queue_end
                ).OnlyEnforceIf(presence)

        if setup_units <= 0:
            continue
        for key, presence in presences.items():
            task_id, instrument_id = key
            if instrument_id != instrument.id:
                continue
            task = task_by_id[task_id]
            for slot, start_unit, end_unit in fixed_by_instrument.get(instrument.id, []):
                fixed_project_id = slot.task.project_id if slot.task else None
                if fixed_project_id == task.project_id:
                    continue
                if task.allow_split:
                    for split_key, unit_presence in split_unit_presences.items():
                        split_task_id, split_instrument_id, unit = split_key
                        if split_task_id != task_id or split_instrument_id != instrument.id:
                            continue
                        if unit < end_unit + setup_units and unit + 1 > start_unit - setup_units:
                            model.Add(unit_presence == 0)
                    continue
                before = model.NewBoolVar(f"t{task_id}_before_fixed_{slot.id}")
                after = model.NewBoolVar(f"t{task_id}_after_fixed_{slot.id}")
                model.Add(before + after == presence)
                model.Add(inst_ends[key] + setup_units <= start_unit).OnlyEnforceIf(before)
                model.Add(inst_starts[key] >= end_unit + setup_units).OnlyEnforceIf(after)
