from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import TimeSlot
from app.services.schedule_completion_service import (
    _mark_task_slots_completed,
    _select_completed_slot,
)


def test_early_started_slot_is_completed_before_planned_start():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        slot = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 7, 13, 14, 30),
            plan_end=datetime(2026, 7, 13, 20, 0),
            actual_start=datetime(2026, 7, 13, 14, 27),
            status="running",
        )
        db.add(slot)
        db.commit()
        end_time = datetime(2026, 7, 13, 14, 28)

        completed_slot = _select_completed_slot([slot], slot.id, end_time)
        _mark_task_slots_completed(db, [slot], completed_slot, end_time)
        db.flush()

        persisted = db.query(TimeSlot).filter(TimeSlot.id == slot.id).one()
        assert persisted.status == "completed"
        assert persisted.actual_start == datetime(2026, 7, 13, 14, 27)
        assert persisted.actual_end == end_time
    finally:
        db.close()


def test_resumed_future_slot_is_selected_over_old_paused_slot():
    old_slot = TimeSlot(
        id=1,
        task_id=1,
        plan_start=datetime(2026, 7, 13, 13, 30),
        plan_end=datetime(2026, 7, 13, 14, 27),
        actual_start=datetime(2026, 7, 13, 13, 32),
        actual_end=datetime(2026, 7, 13, 14, 27),
        status="paused",
    )
    resumed_slot = TimeSlot(
        id=2,
        task_id=1,
        plan_start=datetime(2026, 7, 20, 15, 0),
        plan_end=datetime(2026, 7, 20, 20, 0),
        actual_start=datetime(2026, 7, 13, 14, 32),
        status="running",
    )

    selected = _select_completed_slot(
        [old_slot, resumed_slot], resumed_slot.id, datetime(2026, 7, 13, 14, 33),
    )

    assert selected.id == resumed_slot.id
