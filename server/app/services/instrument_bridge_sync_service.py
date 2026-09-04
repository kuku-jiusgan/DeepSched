from __future__ import annotations

from app.models import InstrumentBridgeReservation, Task, TimeSlot


BRIDGE_SLOT_STATUSES = {
    "scheduled", "running", "blocked", "paused", "interrupted", "completed",
}


def _comparable_datetime(value):
    return value.replace(tzinfo=None) if value is not None and value.tzinfo else value


def rebuild_instrument_bridge_reservations(db, schedule_run_id: str | None = None) -> int:
    """Rebuild all derived bridge reservations from current active slots."""
    db.flush()
    db.query(InstrumentBridgeReservation).delete(synchronize_session=False)
    manual_slots = db.query(TimeSlot).join(Task).filter(
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
        Task.requires_instrument.is_(False),
        Task.requires_human.is_(True),
        Task.assignee_id.isnot(None),
        Task.status.notin_(["completed", "done"]),
    ).order_by(TimeSlot.plan_start, TimeSlot.id).all()
    created = 0
    for slot in manual_slots:
        bridge = _bridge_for_manual_task(db, slot)
        if bridge is None:
            continue
        previous, following = bridge
        db.add(InstrumentBridgeReservation(
            schedule_run_id=schedule_run_id or slot.schedule_run_id,
            task_id=slot.task_id,
            instrument_id=previous.instrument_id,
            previous_task_id=previous.task_id,
            following_task_id=following.task_id,
            plan_start=slot.plan_start,
            plan_end=slot.plan_end,
        ))
        created += 1
    db.flush()
    return created


def valid_bridge_reservations(db, query) -> list[InstrumentBridgeReservation]:
    return [reservation for reservation in query.all() if _is_current(db, reservation)]


def historical_bridge_reservations(db, start_date=None, end_date=None) -> list[dict]:
    """Build read-only bridge intervals from completed manual task execution windows."""
    slots = db.query(TimeSlot).join(Task).filter(
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(["completed", "done"]),
        Task.requires_instrument.is_(False),
        Task.requires_human.is_(True),
        Task.assignee_id.isnot(None),
        Task.status.in_(["completed", "done"]),
        TimeSlot.actual_start.isnot(None),
        TimeSlot.actual_end.isnot(None),
    ).order_by(TimeSlot.actual_start, TimeSlot.id).all()
    result = []
    cache: dict = {}
    for slot in slots:
        bridge = _bridge_for_manual_task(db, slot, cache)
        if bridge is None:
            continue
        actual_start = min(item.actual_start for item in slot.task.time_slots if item.actual_start)
        actual_end = max(item.actual_end for item in slot.task.time_slots if item.actual_end)
        if start_date and _comparable_datetime(actual_end) <= _comparable_datetime(start_date):
            continue
        if end_date and _comparable_datetime(actual_start) >= _comparable_datetime(end_date):
            continue
        previous, following = bridge
        result.append({
            "id": -slot.id,
            "schedule_run_id": slot.schedule_run_id,
            "task_id": slot.task_id,
            "instrument_id": previous.instrument_id,
            "previous_task_id": previous.task_id,
            "following_task_id": following.task_id,
            "plan_start": actual_start,
            "plan_end": actual_end,
            "task": slot.task,
            "kind": "historical_human_bridge",
        })
    return result


def stale_bridge_reservation_ids(
    db,
    schedule_run_id: str | None = None,
) -> list[int]:
    """Return derived bridge reservations that no longer match active task slots."""
    query = db.query(InstrumentBridgeReservation)
    if schedule_run_id is not None:
        query = query.filter(InstrumentBridgeReservation.schedule_run_id == schedule_run_id)
    return [reservation.id for reservation in query.all() if not _is_current(db, reservation)]


def invalidate_task_bridge_reservations(db, task_id: int) -> int:
    return db.query(InstrumentBridgeReservation).filter(
        (InstrumentBridgeReservation.task_id == task_id)
        | (InstrumentBridgeReservation.previous_task_id == task_id)
        | (InstrumentBridgeReservation.following_task_id == task_id)
    ).delete(synchronize_session=False)


def _bridge_for_manual_task(db, slot: TimeSlot, cache: dict | None = None) -> tuple[TimeSlot, TimeSlot] | None:
    # 候选集只取决于负责人，与具体时间槽无关，但这里是逐槽调用的：一次甘特图
    # 请求里同一个人的那条全库扫描会被重复几十遍，实测 9 条桥接要 1.2 秒。
    # cache 按负责人存一次，同一次请求内复用。
    cache = cache if cache is not None else {}
    source_slots = cache.setdefault("source", {}).get(slot.task_id)
    if source_slots is None:
        source_slots = db.query(TimeSlot).filter(
            TimeSlot.task_id == slot.task_id,
            TimeSlot.instrument_id.is_(None),
            TimeSlot.lifecycle_status == "active",
            TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
        ).all()
        cache["source"][slot.task_id] = source_slots
    if not source_slots:
        return None
    source_start = min(item.plan_start for item in source_slots)
    source_end = max(item.plan_end for item in source_slots)
    assignee_id = slot.task.assignee_id
    by_assignee = cache.setdefault("candidates", {})
    if assignee_id not in by_assignee:
        by_assignee[assignee_id] = db.query(TimeSlot).join(Task).filter(
            TimeSlot.lifecycle_status == "active",
            TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
            Task.requires_human.is_(True),
            Task.assignee_id == assignee_id,
        ).order_by(TimeSlot.plan_end.desc(), TimeSlot.id.desc()).all()
    candidates = [item for item in by_assignee[assignee_id] if item.task_id != slot.task_id]
    previous = max(
        (item for item in candidates if (item.actual_end or item.plan_end) <= source_start),
        key=lambda item: (item.actual_end or item.plan_end, item.id),
        default=None,
    )
    if previous is None:
        return None
    following = min(
        (item for item in candidates if item.plan_start >= source_end),
        key=lambda item: (item.plan_start, item.id),
        default=None,
    )
    if (
        following is None
        or previous.instrument_id is None
        or previous.instrument_id != following.instrument_id
        or not previous.task.requires_instrument
        or not following.task.requires_instrument
    ):
        return None
    return previous, following


def _is_current(db, reservation: InstrumentBridgeReservation) -> bool:
    source = db.query(TimeSlot).filter(
        TimeSlot.task_id == reservation.task_id,
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
        TimeSlot.plan_start == reservation.plan_start,
        TimeSlot.plan_end == reservation.plan_end,
    ).first()
    if source is None:
        return False
    bridge = _bridge_for_manual_task(db, source)
    return bool(
        bridge
        and bridge[0].task_id == reservation.previous_task_id
        and bridge[1].task_id == reservation.following_task_id
        and bridge[0].instrument_id == reservation.instrument_id
    )
