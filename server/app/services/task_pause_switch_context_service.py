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
        dependencies: list[tuple[int, int]] = []
        for index, entry in enumerate(self.queue[1:], start=1):
            predecessor = self.queue[index - 1]
            if entry.task.id == predecessor.task.id:
                continue
            if not entry.task.requires_instrument:
                dependencies.append((entry.task.id, predecessor.task.id))
                continue
            if predecessor.task.requires_instrument:
                dependencies.append((entry.task.id, predecessor.task.id))
                continue
            # 下面只处理"前驱是非仪器任务"：它不该挡住后面的仪器任务，所以要么
            # 确认它有资格挡（同仪器同负责人），要么把依赖改挂到更早的仪器任务上。
            # previous_instrument 只服务于这两种判断，此前它的 None 判断挡在最
            # 前面，导致前驱本身就是队首仪器任务时（暂停并切换最常见的形态）顺序
            # 依赖被整条丢掉，求解器随即把被暂停任务的剩余排到了接替任务之前。
            previous_instrument = _previous_instrument_entry(self.queue, predecessor)
            if previous_instrument is None:
                continue
            if _queue_dependency_allowed(self.queue, predecessor, entry):
                dependencies.append((entry.task.id, predecessor.task.id))
            else:
                dependencies.append((entry.task.id, previous_instrument.task.id))
        return list(dict.fromkeys(dependencies))


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
    intermediate_followups, intermediate_groups = _split_intermediate_followups(
        db, intermediate_groups, switch_time, followup_ids,
    )
    replaceable = [slot for slot in source_slots if slot.id != source_slot.id]
    replaceable.extend(slot for slot in target_slots if slot.id != target_slot.id)
    replaceable.extend(slot for group in [*intermediate_groups, *target_followups, *source_followups] for slot in group)
    replaceable.extend(slot for groups in intermediate_followups.values() for group in groups for slot in group)
    # 已经开始或结束的时间槽是既成事实，supersede_slot 会直接拒绝作废它们并
    # 抛异常，整个暂停切换随之变成 500。这里先滤掉，重排只动尚未发生的部分。
    replaceable = [slot for slot in replaceable if slot.actual_start is None and slot.actual_end is None]
    queue = [PauseSwitchQueueEntry(target_slot.task, target_slot, remaining_minutes(target_slot.task, target_slots, switch_time, target_slot), target_slot.status, target_slot)]
    queue.extend(_followup_entries(target_followups))
    queue.append(PauseSwitchQueueEntry(source_slot.task, None, remaining_minutes(source_slot.task, source_slots, switch_time, source_slot), "paused", source_slot))
    queue.extend(_followup_entries(source_followups))
    for group in intermediate_groups:
        queue.append(PauseSwitchQueueEntry(group[0].task, None, slot_minutes(group), group[0].status, group[0]))
        queue.extend(_followup_entries(intermediate_followups.get(group[0].task_id, [])))
    return PauseSwitchContext(switch_time, queue_end, replaceable, queue)


def _split_intermediate_followups(
    db,
    intermediate_groups: list[list[TimeSlot]],
    switch_time: datetime,
    followup_ids: set[int],
) -> tuple[dict[int, list[list[TimeSlot]]], list[list[TimeSlot]]]:
    """把中间任务的连续后续任务从中间队列里摘出来，改挂到各自前驱之后。

    闭包按仪器队列圈定，中间任务的后续往往是不占仪器的方案撰写。它们会以自己
    的时间槽先后混在中间队列里，而队列顺序会被原样当成硬约束交给求解器——一旦
    某个后续任务还停在上一批次的老位置、时间上早于它的前驱，求解器收到的就是
    一条方向完全相反的约束：前驱被迫排到后续之后。实测中这条反向约束把一个
    35 小时的任务顶到了三天以后，中间空出整整两个工作日。

    摘出来重新挂载后，队列顺序与声明的依赖一致，两者都跟着前驱一起重排。
    """
    claimed: set[int] = set()
    result: dict[int, list[list[TimeSlot]]] = {}
    for group in intermediate_groups:
        for followup in target_followup_groups(db, group[0].task, switch_time, CANDIDATE_SLOT_STATUSES):
            followup_task_id = followup[0].task_id
            if followup_task_id in followup_ids or followup_task_id in claimed:
                continue
            claimed.add(followup_task_id)
            result.setdefault(group[0].task_id, []).append(followup)
    remaining = [group for group in intermediate_groups if group[0].task_id not in claimed]
    return result, remaining


def _followup_entries(groups: list[list[TimeSlot]]) -> list[PauseSwitchQueueEntry]:
    return [PauseSwitchQueueEntry(group[0].task, None, remaining_minutes(group[0].task), group[0].status, group[0]) for group in groups]


def _queue_dependency_allowed(
    queue: list[PauseSwitchQueueEntry],
    predecessor: PauseSwitchQueueEntry,
    entry: PauseSwitchQueueEntry,
) -> bool:
    """Do not let an unqualified manual task block another instrument task."""
    if getattr(predecessor.task, "requires_instrument", False) or not getattr(entry.task, "requires_instrument", False):
        return True
    previous_instrument = _previous_instrument_entry(queue, predecessor)
    if previous_instrument is None:
        return False
    return (
        previous_instrument.template_slot.instrument_id == entry.template_slot.instrument_id
        and _same_assignee(predecessor.task, previous_instrument.task, entry.task)
    )


def _previous_instrument_entry(
    queue: list[PauseSwitchQueueEntry],
    predecessor: PauseSwitchQueueEntry,
) -> PauseSwitchQueueEntry | None:
    predecessor_index = next(
        index for index, item in enumerate(queue) if item is predecessor
    )
    return next(
        (
            item for item in reversed(queue[:predecessor_index])
            if item.task.requires_instrument
        ),
        None,
    )


def _same_assignee(*tasks: Task) -> bool:
    assignee_id = tasks[0].assignee_id
    return assignee_id is not None and all(task.assignee_id == assignee_id for task in tasks)
