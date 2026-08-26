from typing import Iterable, Optional

from app.models import Instrument, TimeSlot
from app.services.schedule_slot_change_log_service import record_slot_deleted, supersede_slot


PROTECTED_STATUSES = {"fault", "maintenance"}


def list_instruments_with_effective_status(db, include_unavailable: bool = False):
    query = db.query(Instrument)
    if not include_unavailable:
        query = query.filter(Instrument.availability_status == "available")
    instruments = query.all()
    for instrument in instruments:
        instrument.status = effective_instrument_status(db, instrument)
    return instruments


def effective_instrument_status(db, instrument: Instrument) -> str:
    if instrument.status in PROTECTED_STATUSES:
        return instrument.status
    if _has_running_slot(db, instrument.id):
        return "running"
    return "idle"


def mark_instrument_running(db, instrument_id: Optional[int]) -> None:
    if not instrument_id:
        return
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if instrument and instrument.status not in PROTECTED_STATUSES:
        instrument.status = "running"


def refresh_instrument_status(db, instrument_id: Optional[int]) -> None:
    if not instrument_id:
        return
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if instrument:
        instrument.status = effective_instrument_status(db, instrument)


def refresh_instrument_statuses(db, instrument_ids: Iterable[int | None]) -> None:
    for instrument_id in set(instrument_ids):
        refresh_instrument_status(db, instrument_id)


def delete_time_slots_and_refresh(db, query, synchronize_session=False) -> int:
    slots = query.all()
    instrument_ids = {
        instrument_id
        for instrument_id, in query.with_entities(TimeSlot.instrument_id).distinct().all()
        if instrument_id
    }
    deleted_count = 0
    for slot in slots:
        if slot.actual_start is not None or slot.actual_end is not None:
            continue
        supersede_slot(db, slot, "排程重排")
        slot.status = "cancelled"
        deleted_count += 1
    db.flush()
    refresh_instrument_statuses(db, instrument_ids)
    return deleted_count


def delete_time_slot_and_refresh(db, slot: TimeSlot) -> None:
    instrument_id = slot.instrument_id
    supersede_slot(db, slot, "仪器状态变更")
    slot.status = "cancelled"
    db.flush()
    refresh_instrument_status(db, instrument_id)


def _has_running_slot(db, instrument_id: int) -> bool:
    return db.query(TimeSlot.id).filter(
        TimeSlot.instrument_id == instrument_id,
        TimeSlot.status == "running",
    ).first() is not None
