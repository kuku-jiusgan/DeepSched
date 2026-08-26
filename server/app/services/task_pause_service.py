from __future__ import annotations

from datetime import datetime

from app.domain.errors import DomainConflictError, DomainNotFoundError, DomainValidationError
from app.models import Task, TaskDependency, TaskExecutionSegment, TimeSlot
from app.services.approval_gate_service import unapproved_gate_context
from app.services.audit_log_service import record_audit_log
from app.services.instrument_status_service import refresh_instrument_status
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations
from app.services.project_completion_projection_service import projected_project_completion
from app.services.schedule_delay_service import _load_working_options
from app.services.schedule_forward_slot_service import build_forward_slots
from app.services.schedule_slot_change_log_service import supersede_slot
from app.services.schedule_replan_closure_service import collect_replan_task_ids
from app.services.task_execution_service import ensure_running_state_consistent, predecessors_completed, start_task_execution
from app.services.task_progress_service import planned_task_minutes, remaining_task_minutes
from app.services.task_pause_followup_service import target_followup_groups


CANDIDATE_TASK_STATUSES = {"pending", "scheduled", "paused", "blocked", "interrupted"}
CANDIDATE_SLOT_STATUSES = {"scheduled", "paused", "interrupted", "blocked"}


def list_switch_candidates(db, source_slot_id: int) -> list[dict]:
    source_slot, source_task = _running_source(db, source_slot_id)
    if not source_slot.instrument_id:
        return []

    slots = (
        db.query(TimeSlot)
        .join(Task, Task.id == TimeSlot.task_id)
        .filter(
            TimeSlot.instrument_id == source_slot.instrument_id,
            TimeSlot.task_id != source_task.id,
            TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
            TimeSlot.lifecycle_status == "active",
            TimeSlot.actual_start.is_(None),
            Task.status.in_(CANDIDATE_TASK_STATUSES),
            Task.requires_instrument.is_(True),
            ~Task.children.any(),
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    candidates = []
    seen_task_ids: set[int] = set()
    for slot in slots:
        task = slot.task
        if task.id in seen_task_ids or not predecessors_completed(task):
            continue
        seen_task_ids.add(task.id)
        candidates.append(_candidate_out(slot, task))
    return candidates


def pause_and_switch_task(
    db,
    source_slot_id: int,
    reason: str,
    operator,
    target_slot_id: int | None = None,
) -> dict[str, str]:
    clean_reason = reason.strip()
    if not clean_reason:
        raise DomainValidationError("请填写暂停原因")
    source_slot, source_task = _running_source(db, source_slot_id)
    target_slot = _validated_target(db, source_slot, target_slot_id) if target_slot_id else None

    paused_at = datetime.now()
    paused_slots = _running_task_slots(db, source_slot)
    for slot in paused_slots:
        slot.status = "paused"
    source_slot.actual_end = paused_at
    source_task.executed_minutes = min(
        planned_task_minutes(source_task),
        int(source_task.executed_minutes or 0) + _elapsed_execution_minutes(source_task, paused_at),
    )
    source_task.status = "paused"
    _close_execution_segment(db, source_slot, paused_at, clean_reason, operator.id)
    db.flush()
    for instrument_id in {slot.instrument_id for slot in paused_slots if slot.instrument_id}:
        refresh_instrument_status(db, instrument_id)

    target_task_name = None
    if target_slot:
        target_task_name = target_slot.task.name
        _insert_target_into_source_schedule(db, source_slot, target_slot, paused_at)
        db.flush()
        start_task_execution(db, target_slot.id, operator.id, allow_queue_insert=True)

    record_audit_log(
        db,
        operator.display_name or operator.username,
        "task_paused",
        "task",
        source_task.id,
        {
            "reason": clean_reason,
            "instrument_id": source_slot.instrument_id,
            "source_task_id": source_task.id,
            "source_slot_id": source_slot.id,
            "target_task_id": target_slot.task_id if target_slot else None,
            "target_slot_id": target_slot.id if target_slot else None,
        },
    )
    message = "任务已暂停，仪器已释放"
    if target_task_name:
        message = f"任务已暂停，已切换至【{target_task_name}】"
    return {"status": "ok", "message": message}


def _insert_target_into_source_schedule(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    started_at: datetime,
) -> None:
    switch_time = started_at.replace(second=0, microsecond=0)
    target_slots = _task_queue_slots(db, target_slot)
    source_slots = _task_queue_slots(db, source_slot)
    target_minutes = _remaining_task_minutes(
        target_slot.task,
        target_slots,
        switch_time,
        target_slot,
    )
    source_minutes = _remaining_task_minutes(source_slot.task, source_slots, switch_time, source_slot)
    target_original_end = max(slot.plan_end for slot in target_slots)
    queue_reorder_end = _instrument_queue_end(db, source_slot.instrument_id, switch_time)
    intermediate_groups = _intermediate_task_slots(
        db, source_slot, target_slot, switch_time, queue_reorder_end or target_original_end,
    )
    followup_groups = target_followup_groups(
        db, target_slot.task, switch_time, CANDIDATE_SLOT_STATUSES,
    )
    source_followup_groups = target_followup_groups(
        db, source_slot.task, switch_time, CANDIDATE_SLOT_STATUSES,
    )
    continuous_followup_task_ids = {
        group[0].task_id for group in [*followup_groups, *source_followup_groups]
    }
    intermediate_groups = [
        group for group in intermediate_groups
        if group[0].task_id not in continuous_followup_task_ids
    ]

    historical_source_start = source_slot.actual_start or switch_time
    source_slot.plan_start = min(historical_source_start, switch_time)
    source_slot.plan_end = switch_time
    target_slot.plan_start = switch_time
    target_slot.plan_end = switch_time

    replaceable_slots = [slot for slot in source_slots if slot.id != source_slot.id]
    replaceable_slots.extend(slot for slot in target_slots if slot.id != target_slot.id)
    replaceable_slots.extend(slot for slots in intermediate_groups for slot in slots)
    replaceable_slots.extend(slot for slots in followup_groups for slot in slots)
    replaceable_slots.extend(slot for slots in source_followup_groups for slot in slots)
    for slot in replaceable_slots:
        supersede_slot(db, slot, "暂停切换重排")
    db.flush()

    queue = [
        (target_slot.task, target_slot, target_minutes, target_slot.status, target_slot),
    ]
    queue.append((source_slot.task, None, source_minutes, "paused", source_slot))
    followups = [
        (group[0].task, None, _remaining_task_minutes(group[0].task), group[0].status, group[0])
        for group in followup_groups
    ]
    queue[1:1] = followups
    source_followups = [
        (group[0].task, None, _remaining_task_minutes(group[0].task), group[0].status, group[0])
        for group in source_followup_groups
    ]
    queue[1 + len(followups) + 1:1 + len(followups) + 1] = source_followups
    queue.extend(
        (slots[0].task, None, _slot_minutes(slots), slots[0].status, slots[0])
        for slots in intermediate_groups
    )
    options = _load_working_options(db, switch_time)
    instrument_ends: dict[int, datetime] = {}
    assignee_ends: dict[int, datetime] = {}
    for task, reusable_slot, duration_minutes, status, template_slot in queue:
        if duration_minutes <= 0:
            continue
        resource_bounds = [switch_time]
        if template_slot.instrument_id is not None:
            resource_bounds.append(instrument_ends.get(template_slot.instrument_id, switch_time))
        if task.requires_human and task.assignee_id is not None:
            resource_bounds.append(assignee_ends.get(task.assignee_id, switch_time))
        base_start = max(resource_bounds)
        earliest_start = max(
            base_start,
            _dependency_ready_time(db, task) or base_start,
            _approval_ready_time(db, task) or base_start,
        )
        ranges = build_forward_slots(
            db,
            task,
            template_slot.instrument_id,
            duration_minutes,
            earliest_start,
            options,
        )
        if not ranges:
            ranges_without_deadline = build_forward_slots(
                db,
                task,
                template_slot.instrument_id,
                duration_minutes,
                earliest_start,
                options,
            )
            is_deadline_conflict = bool(
                task.project
                and task.project.end_date
                and ranges_without_deadline
                and ranges_without_deadline[-1][1] > task.project.end_date
            )
            raise DomainConflictError(
                _reorder_conflict_message(
                    source_slot.task,
                    task,
                    duration_minutes,
                    earliest_start,
                    is_deadline_conflict,
                )
            )
        _save_reordered_ranges(
            db,
            task,
            reusable_slot,
            ranges,
            template_slot.instrument_id,
            status,
            template_slot,
        )
        db.flush()
        task_end = ranges[-1][1]
        if template_slot.instrument_id is not None:
            instrument_ends[template_slot.instrument_id] = task_end
        if task.requires_human and task.assignee_id is not None:
            assignee_ends[task.assignee_id] = task_end
    _ensure_reordered_projects_within_deadline(
        db,
        {task.project for task, *_ in queue if task.project and task.project.end_date},
        options,
    )
    rebuild_instrument_bridge_reservations(db)


def _approval_ready_time(db, task: Task) -> datetime | None:
    bounds, _ = unapproved_gate_context(db, [task])
    return bounds.get(task.id)


def _dependency_ready_time(db, task: Task) -> datetime | None:
    ready_times: list[datetime] = []
    for dependency in task.predecessors:
        predecessor = dependency.predecessor
        if predecessor.is_external_gate:
            continue
        slots = db.query(TimeSlot).filter(
            TimeSlot.task_id == predecessor.id,
            TimeSlot.lifecycle_status == "active",
        ).all()
        actual_ends = [slot.actual_end for slot in slots if slot.actual_end]
        plan_ends = [slot.plan_end for slot in slots]
        if predecessor.status in {"done", "completed"} and actual_ends:
            ready_times.append(max(actual_ends))
        elif plan_ends:
            ready_times.append(max(plan_ends))
    return max(ready_times, default=None)


def _reorder_conflict_message(
    current_task: Task,
    task: Task,
    duration_minutes: int,
    earliest_start: datetime,
    is_deadline_conflict: bool,
) -> str:
    task_label = _task_label_with_top_level(task)
    project_code = task.project.code if task.project else "未知项目"
    project_name = task.project.name if task.project else "未知项目"
    deadline = task.project.end_date if task.project else None
    deadline_text = deadline.strftime("%Y-%m-%d %H:%M") if deadline else "未设置项目截止时间"
    if deadline and (earliest_start >= deadline or is_deadline_conflict):
        reason = "已超出项目结题日期"
        suggestion = "建议进行项目延期后再进行排程"
    else:
        reason = "没有满足仪器、人员或连续作业约束的可用时段"
        suggestion = "请调整资源安排或项目时间后再进行排程"
    return (
        f"【重排失败】{current_task.name} 无法重排\n\n"
        f"失败原因：任务【{task_label}】{reason}\n"
        f"所属项目：{project_code} · {project_name}\n"
        f"结题时间：{deadline.strftime('%Y-%m-%d') if deadline else '未设置'}\n"
        f"{suggestion}"
    )


def _task_label_with_top_level(task: Task) -> str:
    names = [task.name]
    current = task
    seen: set[int] = set()
    while current.parent_id and current.parent_id not in seen:
        seen.add(current.id)
        parent = current.parent
        if parent is None:
            break
        names.append(parent.name)
        current = parent
    return " · ".join(reversed(names))


def _instrument_queue_end(db, instrument_id: int | None, switch_time: datetime) -> datetime | None:
    if instrument_id is None:
        return None
    return max(
        (
            slot.plan_end
            for slot in db.query(TimeSlot).filter(
                TimeSlot.instrument_id == instrument_id,
                TimeSlot.plan_start >= switch_time,
                TimeSlot.actual_start.is_(None),
                TimeSlot.status.in_(CANDIDATE_SLOT_STATUSES),
                TimeSlot.lifecycle_status == "active",
            ).all()
        ),
        default=None,
    )


def _ensure_reordered_projects_within_deadline(db, projects: set, options: dict) -> None:
    for project in projects:
        projected_end = projected_project_completion(db, project, options)
        if projected_end <= project.end_date:
            continue
        label = " · ".join(part for part in [project.code, project.name] if part)
        raise DomainConflictError(
            f"此次切换预计导致项目【{label}】最晚于 {projected_end:%Y-%m-%d %H:%M} 完成，"
            f"超过项目截止时间 {project.end_date:%Y-%m-%d %H:%M}，禁止切换！"
        )


def _task_queue_slots(db, anchor: TimeSlot) -> list[TimeSlot]:
    slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == anchor.task_id,
            TimeSlot.instrument_id == anchor.instrument_id,
            TimeSlot.status.in_(["scheduled", "running", "paused", "blocked", "interrupted"]),
            TimeSlot.actual_end.is_(None),
            TimeSlot.lifecycle_status == "active",
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    if all(slot.id != anchor.id for slot in slots):
        slots.append(anchor)
    return sorted(slots, key=lambda slot: (slot.plan_start, slot.id))


def _intermediate_task_slots(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    switch_time: datetime,
    target_original_end: datetime,
) -> list[list[TimeSlot]]:
    seed_task_ids = {source_slot.task_id, target_slot.task_id}
    assignee_ids = {
        task.assignee_id
        for task in (source_slot.task, target_slot.task)
        if task.requires_human and task.assignee_id is not None
    }
    closure_ids = collect_replan_task_ids(
        db,
        seed_task_ids,
        {source_slot.instrument_id} if source_slot.instrument_id is not None else set(),
        assignee_ids,
        switch_time,
    ) - seed_task_ids
    closure_ids -= _dependency_descendant_ids(db, seed_task_ids)
    if not closure_ids:
        return []
    slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id.in_(closure_ids),
            TimeSlot.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_end > switch_time,
            TimeSlot.lifecycle_status == "active",
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    grouped: dict[int, list[TimeSlot]] = {}
    for slot in slots:
        grouped.setdefault(slot.task_id, []).append(slot)
    if not grouped:
        return []
    all_slots = (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id.in_(grouped),
            TimeSlot.status.in_(["scheduled", "paused", "blocked", "interrupted"]),
            TimeSlot.actual_start.is_(None),
            TimeSlot.plan_start >= switch_time,
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )
    complete_groups: dict[int, list[TimeSlot]] = {task_id: [] for task_id in grouped}
    for slot in all_slots:
        complete_groups[slot.task_id].append(slot)
    return [complete_groups[task_id] for task_id in grouped]


def _dependency_descendant_ids(db, seed_task_ids: set[int]) -> set[int]:
    descendants: set[int] = set()
    frontier = set(seed_task_ids)
    while frontier:
        rows = db.query(TaskDependency.task_id).filter(
            TaskDependency.predecessor_id.in_(frontier),
        ).all()
        next_ids = {task_id for (task_id,) in rows} - descendants - seed_task_ids
        if not next_ids:
            break
        descendants.update(next_ids)
        frontier = next_ids
    return descendants


def _slot_minutes(slots: list[TimeSlot]) -> int:
    return sum(
        max(0, int((slot.plan_end - slot.plan_start).total_seconds() / 60))
        for slot in slots
    )


def _remaining_slot_minutes(
    slots: list[TimeSlot], from_time: datetime, active_slot: TimeSlot,
) -> int:
    """Return only the unexecuted portion of a task queue from the switch time."""
    minutes = 0
    for slot in slots:
        if slot.id == active_slot.id and slot.actual_start is None:
            start = slot.plan_start
        else:
            if slot.plan_end <= from_time:
                continue
            start = max(slot.plan_start, from_time)
        minutes += max(0, int((slot.plan_end - start).total_seconds() / 60))
    return minutes


def _remaining_task_minutes(
    task: Task,
    legacy_slots: list[TimeSlot] | None = None,
    switch_time: datetime | None = None,
    active_slot: TimeSlot | None = None,
) -> int:
    """Use the task progress ledger as the only source of remaining work."""
    if task.est_duration_hours is None and legacy_slots is not None and switch_time and active_slot:
        return _remaining_slot_minutes(legacy_slots, switch_time, active_slot)
    return remaining_task_minutes(task)


def _save_reordered_ranges(
    db,
    task: Task,
    reusable_slot: TimeSlot | None,
    ranges: list[tuple[datetime, datetime]],
    instrument_id: int | None,
    status: str,
    template: TimeSlot,
) -> None:
    for index, (start, end) in enumerate(ranges):
        slot = reusable_slot if index == 0 and reusable_slot is not None else TimeSlot(
            task_id=task.id,
            schedule_run_id=template.schedule_run_id,
            instrument_id=instrument_id,
            tier=template.tier,
        )
        slot.plan_start = start
        slot.plan_end = end
        slot.status = status
        if slot is not reusable_slot:
            db.add(slot)


def _running_source(db, slot_id: int) -> tuple[TimeSlot, Task]:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise DomainNotFoundError("时间槽不存在")
    task = slot.task
    if not task:
        raise DomainNotFoundError("任务不存在")
    if task.status == "running":
        active_slot = _actual_running_slot(task, slot.instrument_id)
        if active_slot is not None:
            slot = active_slot
        try:
            ensure_running_state_consistent(task, slot)
        except DomainConflictError:
            raise DomainConflictError("任务与时间槽状态不一致，请刷新工作台后重试")
    if task.status != "running" or slot.actual_end is not None:
        raise DomainConflictError("只有正在运行且占用仪器的任务可以暂停")
    return slot, task


def _actual_running_slot(task: Task, instrument_id: int | None) -> TimeSlot | None:
    return next(
        (
            item for item in task.time_slots
            if item.instrument_id == instrument_id
            and item.status == "running"
            and item.lifecycle_status == "active"
            and item.actual_start is not None
            and item.actual_end is None
        ),
        None,
    )


def _validated_target(db, source_slot: TimeSlot, target_slot_id: int) -> TimeSlot:
    candidates = list_switch_candidates(db, source_slot.id)
    candidate_ids = {candidate["slot_id"] for candidate in candidates}
    if target_slot_id not in candidate_ids:
        raise DomainConflictError("接替任务不满足仪器、状态或前置任务条件")
    return db.query(TimeSlot).filter(TimeSlot.id == target_slot_id).first()


def _running_task_slots(db, source_slot: TimeSlot) -> list[TimeSlot]:
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.task_id == source_slot.task_id,
            TimeSlot.status == "running",
            TimeSlot.lifecycle_status == "active",
            TimeSlot.plan_end >= source_slot.plan_start,
        )
        .order_by(TimeSlot.plan_start, TimeSlot.id)
        .all()
    )


def _close_execution_segment(
    db,
    slot: TimeSlot,
    ended_at: datetime,
    reason: str,
    operator_id: int,
) -> None:
    segment = (
        db.query(TaskExecutionSegment)
        .filter(
            TaskExecutionSegment.task_id == slot.task_id,
            TaskExecutionSegment.ended_at.is_(None),
        )
        .order_by(TaskExecutionSegment.started_at.desc(), TaskExecutionSegment.id.desc())
        .first()
    )
    if segment:
        segment.ended_at = ended_at
        segment.end_reason = "paused"
        segment.pause_reason = reason
        return
    db.add(TaskExecutionSegment(
        task_id=slot.task_id,
        slot_id=slot.id,
        instrument_id=slot.instrument_id,
        operator_id=operator_id,
        started_at=slot.actual_start,
        ended_at=ended_at,
        end_reason="paused",
        pause_reason=reason,
    ))


def _elapsed_execution_minutes(task: Task, ended_at: datetime) -> int:
    segment = next(
        (item for item in reversed(task.execution_segments) if item.ended_at is None),
        None,
    )
    if not segment:
        return 0
    return max(0, int((ended_at - segment.started_at).total_seconds() // 60))


def _candidate_out(slot: TimeSlot, task: Task) -> dict:
    project = task.project
    return {
        "slot_id": slot.id,
        "task_id": task.id,
        "task_name": task.name,
        "project_code": project.code if project else "-",
        "project_name": project.name if project else "-",
        "assignee_name": task.assignee.display_name if task.assignee else None,
        "plan_start": slot.plan_start,
        "plan_end": slot.plan_end,
        "is_paused": task.status == "paused",
    }
