from typing import Iterable, Optional

from app.models import Instrument, Task, TimeSlot
from app.services.instrument_occupancy_service import (
    ACTIVE_SLOT_STATUSES, current_occupying_slot,
)
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
    return "running" if instrument_is_working(db, instrument.id) else "idle"


def instrument_is_working(db, instrument_id: int) -> bool:
    """展示口径：这台仪器上只要还有仪器任务在进行中，就算运作中。

    早先只看"有没有一个实际开始了、还没结束的时间槽"。这一条漏掉了跨时段的空档：
    一个任务周五开工，当天那段按计划边界收了尾，剩下几段排在下周一，任务状态仍是
    进行中——中间这段时间两个界面都显示空闲，而任务并没有做完。用户要的是"只要
    这个仪器还有任务在运行中，就显示运作中"。

    两条判据取并集，只会把空闲改成运作中，不会反过来。这个口径**只服务于状态
    展示**：排程判定用的是 current_occupying_task（那问的是"此刻这台仪器物理上被
    谁占着"），各类统计口径也一概不受影响。
    """
    if current_occupying_slot(db, instrument_id) is not None:
        return True
    return running_instrument_task(db, instrument_id) is not None


def running_instrument_task(db, instrument_id: int) -> Task | None:
    """这台仪器上处于「进行中」的仪器任务，没有则返回 None。"""
    return _running_instrument_task_query(db).filter(
        TimeSlot.instrument_id == instrument_id,
    ).order_by(TimeSlot.plan_start, TimeSlot.id).first()


def running_instrument_tasks(db, instrument_ids: Iterable[int]) -> dict[int, Task]:
    """按仪器批量取「进行中」的仪器任务，供实验室状态一次性列出所有仪器时使用。"""
    ids = [instrument_id for instrument_id in instrument_ids if instrument_id]
    if not ids:
        return {}
    rows = _running_instrument_task_query(db, with_instrument=True).filter(
        TimeSlot.instrument_id.in_(ids),
    ).order_by(TimeSlot.plan_start, TimeSlot.id).all()
    result: dict[int, Task] = {}
    for task, instrument_id in rows:
        result.setdefault(instrument_id, task)
    return result


def _running_instrument_task_query(db, with_instrument: bool = False):
    entities = (Task, TimeSlot.instrument_id) if with_instrument else (Task,)
    return (
        db.query(*entities)
        .join(TimeSlot, TimeSlot.task_id == Task.id)
        .filter(
            Task.status == "running",
            Task.requires_instrument.is_(True),
            TimeSlot.lifecycle_status == "active",
            TimeSlot.status.in_(ACTIVE_SLOT_STATUSES),
        )
    )


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
        if slot.actual_end is not None:
            continue
        if slot.actual_start is None:
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
