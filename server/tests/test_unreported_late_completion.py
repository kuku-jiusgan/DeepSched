from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    AuditLog, Instrument, Notification, Project, Task, TaskDependency,
    TaskExecutionSegment, TimeSlot, User,
)
from app.repositories.task_delay_repository import has_reported_task_delay
from app.services.schedule_completion_service import complete_task_and_shift
from app.services.schedule_delay_propagation_service import propagate_actual_delay
from app.services.schedule_delay_service import report_task_delay


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def make_queue(db, relation: str, tier: str):
    project = Project(
        code="SOURCE", name="Source project",
        start_date=datetime(2026, 9, 1), end_date=datetime(2026, 10, 1),
    )
    next_project = Project(
        code="NEXT", name="Next project",
        start_date=datetime(2026, 9, 1), end_date=datetime(2026, 10, 1),
    )
    user = User(username="operator", display_name="Operator", role="分析员")
    instrument = Instrument(code="INSTRUMENT", name="Instrument")
    source = Task(
        project=project, name="Source task", task_type="test", status="running",
        requires_human=relation == "assignee", assignee=user,
        requires_instrument=relation == "instrument", est_duration_hours=4,
        delay_status="delayed",
    )
    following = Task(
        project=next_project, name="Following task", task_type="test",
        status="scheduled", requires_human=relation == "assignee", assignee=user,
        requires_instrument=True, est_duration_hours=24,
    )
    db.add_all([source, following, instrument])
    db.flush()
    source.instrument_ids = [instrument.id] if source.requires_instrument else []
    following.instrument_ids = [instrument.id]
    if relation == "dependency":
        db.add(TaskDependency(task_id=following.id, predecessor_id=source.id))
    source_slot = TimeSlot(
        task=source, instrument=instrument if source.requires_instrument else None,
        plan_start=datetime(2026, 9, 8, 8, 30),
        plan_end=datetime(2026, 9, 8, 12, 30),
        actual_start=datetime(2026, 9, 8, 8, 30), status="running",
    )
    db.add(source_slot)
    db.flush()
    segment = TaskExecutionSegment(
        task=source, slot=source_slot,
        started_at=source_slot.actual_start,
        instrument_id=source_slot.instrument_id, operator_id=user.id,
    )
    db.add(segment)
    for start, end in [
        (datetime(2026, 9, 8, 16, 30), datetime(2026, 9, 8, 20)),
        (datetime(2026, 9, 9, 8, 30), datetime(2026, 9, 9, 20)),
        (datetime(2026, 9, 10, 8, 30), datetime(2026, 9, 10, 17, 30)),
    ]:
        db.add(TimeSlot(
            task=following, instrument=instrument, plan_start=start, plan_end=end,
            tier=tier, status="scheduled",
        ))
    db.commit()
    return source, source_slot, segment, following


def slot_snapshot(task):
    return [
        (s.id, s.plan_start, s.plan_end, s.status, s.tier, s.lifecycle_status)
        for s in task.time_slots
    ]


@pytest.mark.parametrize("relation", ["assignee", "instrument", "dependency"])
@pytest.mark.parametrize("tier", ["frozen", "confirmed"])
@pytest.mark.parametrize("release_instrument", [False, True])
def test_unreported_late_completion_preserves_following_schedule(
    db, relation, tier, release_instrument,
):
    source, source_slot, segment, following = make_queue(db, relation, tier)
    original = slot_snapshot(following)
    completed_at = datetime(2026, 9, 9, 9, 15, 46)
    # A delay report belonging to another task must not authorize propagation.
    db.add(AuditLog(
        user_name="operator", action="task_delay_reported", target_type="time_slot",
        target_id=following.time_slots[0].id,
        detail={"task_id": following.id, "delay_hours": 1, "reason": "实验延期"},
    ))
    db.commit()

    result = complete_task_and_shift(
        db, source.id, actual_end_time=completed_at,
        completed_slot_id=source_slot.id, release_instrument=release_instrument,
    )
    db.commit()
    db.expire_all()

    assert result["status"] == "ok"
    assert result["delayed_slots"] == result["delay_affected_tasks"] == 0
    assert result["moved_tasks"] == 0
    assert result["released_instrument"] is release_instrument
    assert source.status == source_slot.status == "completed"
    assert source.delay_status == "delayed"
    assert source_slot.actual_end == segment.ended_at == completed_at
    assert segment.end_reason == "completed"
    assert source_slot.plan_end == datetime(2026, 9, 8, 12, 30)
    assert slot_snapshot(following) == original
    assert following.status == "scheduled"
    assert db.query(Notification).count() == 0


def test_direct_propagation_requires_a_report_not_overdue_status_or_extra_minutes(db):
    source, _, _, following = make_queue(db, "assignee", "confirmed")
    source.additional_planned_minutes = 120
    original = slot_snapshot(following)
    result = propagate_actual_delay(
        db, source, datetime(2026, 9, 8, 12, 30), datetime(2026, 9, 9, 9, 15),
    )
    db.flush()
    db.expire_all()
    assert result["affected_tasks"] == 0
    assert slot_snapshot(following) == original


def test_submitted_delay_is_recognized_after_slot_replacement(db):
    source, source_slot, _, following = make_queue(db, "instrument", "frozen")
    report_task_delay(db, source_slot.id, 5, "实验延期", "operator")
    db.commit()
    assert source.additional_planned_minutes == 300
    assert has_reported_task_delay(db, source.id)
    assert not has_reported_task_delay(db, following.id)

    # Reports identify the task even after their original slot is superseded.
    source_slot.lifecycle_status = "superseded"
    source.additional_planned_minutes = 0
    db.commit()
    assert has_reported_task_delay(db, source.id)
