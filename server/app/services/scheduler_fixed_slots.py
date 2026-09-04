from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace

from ortools.sat.python import cp_model

from sqlalchemy.orm import joinedload

from app.models import InstrumentBridgeReservation, TimeSlot
from app.services.scheduler_helpers import datetime_to_units


FIXED_SLOT_STATUSES = ["scheduled", "running", "completed", "paused", "blocked", "interrupted"]


def snapshot_fixed_slots(snapshot_slots) -> list:
    """Adapt immutable snapshot rows to the minimal slot/task interface."""
    return [
        SimpleNamespace(
            id=row.id, task_id=row.task_id, instrument_id=row.instrument_id,
            plan_start=row.plan_start, plan_end=row.plan_end,
            actual_start=row.actual_start, actual_end=row.actual_end,
            tier=row.tier, status=row.status, lifecycle_status=row.lifecycle_status,
            task=SimpleNamespace(
                requires_human=row.task_requires_human,
                assignee_id=row.task_assignee_id,
            ),
        )
        for row in snapshot_slots
        if row.status in FIXED_SLOT_STATUSES and row.lifecycle_status == "active"
    ]


def _fixed_slot_range(slot: TimeSlot | InstrumentBridgeReservation) -> tuple[datetime, datetime]:
    if isinstance(slot, InstrumentBridgeReservation):
        return slot.plan_start, slot.plan_end
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
        or (slot.actual_start is not None and slot.actual_end is None)
    )


def load_fixed_slots(
    db,
    excluded_task_ids: set[int] | None = None,
    relevant_instrument_ids: set[int] | None = None,
    relevant_assignee_ids: set[int] | None = None,
    slot_rows: list[TimeSlot] | None = None,
) -> list[TimeSlot]:
    # 下面按 slot.task.requires_human / assignee_id 过滤，不预加载的话每个时间槽
    # 都会触发一次单独的 task 查询——实测一次排程里仅此一处就发了 142 条 SQL。
    if slot_rows is not None:
        slots = list(slot_rows)
    else:
        query = db.query(TimeSlot).options(joinedload(TimeSlot.task)).filter(
        TimeSlot.status.in_(FIXED_SLOT_STATUSES),
        TimeSlot.lifecycle_status == "active",
        )
        slots = query.order_by(TimeSlot.instrument_id, TimeSlot.plan_start, TimeSlot.id).all()
    fixed_slots = [
        slot for slot in slots
        if (slot.status != "completed" or (slot.actual_start and slot.actual_end))
        # A running status alone is not evidence of resource occupancy. This
        # can occur on historical continuation slots created before execution
        # state was normalized; only an actual start or an explicit frozen lock
        # may reserve capacity during a replan.
        and not (
            slot.status == "running"
            and slot.actual_start is None
            and slot.tier != "frozen"
        )
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
            getattr(slot, "task", None) is not None
            and slot.task.requires_human
            and slot.task.assignee_id in assignee_ids
        )
        or (
            getattr(slot, "task_requires_human", False)
            and getattr(slot, "task_assignee_id", None) in assignee_ids
        )
    ]


def load_fixed_bridge_reservations(
    db,
    excluded_task_ids: set[int] | None = None,
    relevant_instrument_ids: set[int] | None = None,
) -> list[InstrumentBridgeReservation]:
    query = db.query(InstrumentBridgeReservation)
    if excluded_task_ids:
        query = query.filter(~InstrumentBridgeReservation.task_id.in_(excluded_task_ids))
    if relevant_instrument_ids is not None:
        query = query.filter(InstrumentBridgeReservation.instrument_id.in_(relevant_instrument_ids))
    return query.order_by(
        InstrumentBridgeReservation.instrument_id,
        InstrumentBridgeReservation.plan_start,
        InstrumentBridgeReservation.id,
    ).all()


def snapshot_bridge_reservations(rows) -> list:
    """Adapt immutable bridge snapshots to the constraint interface."""
    return [
        SimpleNamespace(
            id=row.id, task_id=row.task_id, instrument_id=row.instrument_id,
            previous_task_id=row.previous_task_id, following_task_id=row.following_task_id,
            plan_start=row.plan_start, plan_end=row.plan_end,
        )
        for row in rows
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
    fixed_bridge_reservations: list[InstrumentBridgeReservation] | None = None,
    maintenance_windows: list[tuple[int, tuple[int, int]]] | None = None,
) -> None:
    fixed_by_instrument: dict[int, list[tuple[TimeSlot | InstrumentBridgeReservation, int, int]]] = defaultdict(list)
    fixed_bridge_reservations = fixed_bridge_reservations or []
    maintenance_windows = maintenance_windows or []
    for slot in [*fixed_slots, *fixed_bridge_reservations]:
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
        for index, (start_unit, end_unit) in enumerate(
            window
            for instrument_id, window in maintenance_windows
            if instrument_id == instrument.id
        ):
            if end_unit <= start_unit:
                continue
            instrument_intervals.append(model.NewIntervalVar(
                start_unit,
                end_unit - start_unit,
                end_unit,
                f"maintenance_window_i{instrument.id}_{index}",
            ))
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
            if isinstance(slot, TimeSlot) and _is_protected_slot(slot)
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
