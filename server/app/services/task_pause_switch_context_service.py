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
    source_task_id: int
    target_task_id: int
    target_slot_id: int
    current_project_id: int
    instrument_id: int | None

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
        """本次被暂停的源任务。

        这个值会作为 preserved_status_task_ids 交给落地环节——不在这个名单里的
        running/paused 任务会被整个跳过，一个时间槽都不落。早先它是靠
        `next(entry.status == "paused")` 从队列里猜的，而队列第一个元素是目标
        任务、状态直接取自目标时间槽。切换到一个此前已被暂停的任务（界面上带
        「恢复」标签的候选）时，目标自己就是 paused，于是猜到的是目标而不是源，
        真正的源任务落不进保留名单，剩余工时被静默丢弃：排程报成功，任务却一个
        时间槽都没有。源任务必须直接记下来，不能靠状态反推。
        """
        return self.source_task_id

    @property
    def queue_dependencies(self) -> list[tuple[int, int]]:
        """锁住这台仪器上各任务的先后，仅此而已。

        这条顺序约束存在的理由只有两个：不锁的话求解器会把被暂停任务的剩余工时
        排到接替任务之前，这次切换就白切了；而暂停切换是个局部动作，不该把仪器
        队列后面那些别的项目的先后打乱。两个理由讲的都是**同一台仪器上的排队**。

        此前这里把整条闭包队列相邻两两串成硬链，不占仪器的任务也在里面。闭包不
        只按仪器圈定——task_pause_window_service._assignee_slots 会把同一个负责人
        的人工任务一并拉进来，于是"方案撰写"这类根本不碰仪器的任务也被钉在了某个
        固定位置上。方案撰写要跟着自己的方法开发走，这是项目计划创建时就写下的
        continuous_successor 关系（见 task_dependency_service），求解器本来就当
        硬前置执行，外加一条"做完接着做"的软目标——不需要队列再锁一遍。

        真正的坏处出在**前驱不在闭包里**的那些人工任务身上：它们没有可跟随的前驱，
        却被硬钉在两个不相干项目的仪器任务中间。实测就是这么把四个互不相关的项目
        串成一条链，切换随即判定不可行；而放开之后，两个方案撰写仍然紧跟各自的
        方法开发，仪器队列的先后也没有变。

        "别的项目会被打乱"这层担心另有更靠谱的东西兜着：项目结题日期是硬约束，
        真把别人顶超期了，排程会当场失败并指名道姓，不必靠钉死位置来防。
        """
        competing = [
            entry for entry in self.queue
            if entry.task.requires_instrument
            and entry.template_slot.instrument_id == self.instrument_id
        ]
        dependencies = [
            (competing[index].task.id, competing[index - 1].task.id)
            for index in range(1, len(competing))
            if competing[index].task.id != competing[index - 1].task.id
        ]
        # 目标任务的连续后续必须在暂停任务恢复前完成。普通的业务前置只保证
        # “方法开发结束后才能开始方案撰写”，但不会阻止暂停任务插在两者之间；
        # 切换动作既然已经选定目标任务，就把目标后续链作为一个整体推进。
        target_index = next(
            (index for index, entry in enumerate(self.queue)
             if entry.task.id == self.target_task_id),
            None,
        )
        source_index = next(
            (index for index, entry in enumerate(self.queue)
             if entry.task.id == self.source_task_id),
            None,
        )
        if target_index is not None and source_index is not None and target_index < source_index:
            target_followups = self.queue[target_index + 1:source_index]
            if target_followups:
                dependencies.append((
                    self.source_task_id,
                    target_followups[-1].task.id,
                ))
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
    return PauseSwitchContext(
        switch_time, queue_end, replaceable, queue,
        source_slot.task_id, target_slot.task_id, target_slot.id,
        source_slot.task.project_id, source_slot.instrument_id,
    )


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
