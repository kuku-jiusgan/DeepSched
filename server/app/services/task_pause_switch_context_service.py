from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models import Task, TimeSlot
from app.services.task_pause_followup_service import target_followup_groups
from app.services.task_pause_window_service import (
    CANDIDATE_SLOT_STATUSES, instrument_queue_end, intermediate_task_slots,
    remaining_minutes, slot_minutes, task_queue_slots,
)


@dataclass(frozen=True)
class PauseSwitchQueueEntry:
    task: Task
    reusable_slot: TimeSlot | None
    duration_minutes: int
    status: str
    template_slot: TimeSlot


@dataclass(frozen=True)
class PauseSwitchContext:
    switch_time: datetime
    queue_end: datetime
    replaceable_slots: list[TimeSlot]
    queue: list[PauseSwitchQueueEntry]

    @property
    def task_ids(self) -> set[int]:
        return {entry.task.id for entry in self.queue}

    @property
    def remaining_duration_minutes(self) -> dict[int, int]:
        return {
            entry.task.id: entry.duration_minutes
            for entry in self.queue
            if entry.duration_minutes > 0
        }

    @property
    def paused_source_task_id(self) -> int:
        return next(entry.task.id for entry in self.queue if entry.status == "paused")

    @property
    def queue_dependencies(self) -> list[tuple[int, int]]:
        return [
            (entry.task.id, predecessor.task.id)
            for predecessor, entry in zip(self.queue, self.queue[1:])
            if entry.task.id != predecessor.task.id
        ]


def build_pause_switch_context(db, source_slot: TimeSlot, target_slot: TimeSlot, started_at: datetime) -> PauseSwitchContext:
    switch_time = started_at.replace(second=0, microsecond=0)
    target_slots = task_queue_slots(db, target_slot)
    source_slots = task_queue_slots(db, source_slot)
    target_end = max(slot.plan_end for slot in target_slots)
    queue_end = instrument_queue_end(db, source_slot.instrument_id, switch_time) or target_end
    intermediate_groups = intermediate_task_slots(db, source_slot, target_slot, switch_time, queue_end)
    target_followups = target_followup_groups(db, target_slot.task, switch_time, CANDIDATE_SLOT_STATUSES)
    source_followups = target_followup_groups(db, source_slot.task, switch_time, CANDIDATE_SLOT_STATUSES)
    followup_ids = {group[0].task_id for group in [*target_followups, *source_followups]}
    intermediate_groups = [group for group in intermediate_groups if group[0].task_id not in followup_ids]
    replaceable = [slot for slot in source_slots if slot.id != source_slot.id]
    replaceable.extend(slot for slot in target_slots if slot.id != target_slot.id)
    replaceable.extend(slot for group in [*intermediate_groups, *target_followups, *source_followups] for slot in group)
    queue = [PauseSwitchQueueEntry(target_slot.task, target_slot, remaining_minutes(target_slot.task, target_slots, switch_time, target_slot), target_slot.status, target_slot)]
    queue.extend(_followup_entries(target_followups))
    queue.append(PauseSwitchQueueEntry(source_slot.task, None, remaining_minutes(source_slot.task, source_slots, switch_time, source_slot), "paused", source_slot))
    queue.extend(_followup_entries(source_followups))
    queue.extend(PauseSwitchQueueEntry(group[0].task, None, slot_minutes(group), group[0].status, group[0]) for group in intermediate_groups)
    return PauseSwitchContext(switch_time, queue_end, replaceable, queue)


def _followup_entries(groups: list[list[TimeSlot]]) -> list[PauseSwitchQueueEntry]:
    return [PauseSwitchQueueEntry(group[0].task, None, remaining_minutes(group[0].task), group[0].status, group[0]) for group in groups]
