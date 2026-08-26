from __future__ import annotations

from app.models import InstrumentBridgeReservation, Task, TimeSlot


BRIDGE_SLOT_STATUSES = {
    "scheduled", "running", "blocked", "paused", "interrupted", "completed",
}


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


def invalidate_task_bridge_reservations(db, task_id: int) -> int:
    return db.query(InstrumentBridgeReservation).filter(
        (InstrumentBridgeReservation.task_id == task_id)
        | (InstrumentBridgeReservation.previous_task_id == task_id)
        | (InstrumentBridgeReservation.following_task_id == task_id)
    ).delete(synchronize_session=False)


def _bridge_for_manual_task(db, slot: TimeSlot) -> tuple[TimeSlot, TimeSlot] | None:
    source_slots = db.query(TimeSlot).filter(
        TimeSlot.task_id == slot.task_id,
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
    ).all()
    source_start = min(item.plan_start for item in source_slots)
    source_end = max(item.plan_end for item in source_slots)
    candidates = db.query(TimeSlot).join(Task).filter(
        TimeSlot.task_id != slot.task_id,
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
        Task.requires_human.is_(True),
        Task.assignee_id == slot.task.assignee_id,
    ).order_by(TimeSlot.plan_end.desc(), TimeSlot.id.desc()).all()
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
