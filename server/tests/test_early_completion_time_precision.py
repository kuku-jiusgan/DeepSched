from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot, User
from app.services.schedule_completion_service import complete_task_and_shift


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 9, 10, 0, 21, tzinfo=tz)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("cross_project", [False, True])
def test_completion_with_seconds_advances_manual_successor(db, cross_project):
    operator = User(username="precision-tech", display_name="技术员", role="技术员")
    project = Project(code="PRECISION", name="方法研究项目")
    instrument = Instrument(code="PRECISION-I", name="测试仪器")
    db.add_all([operator, project, instrument])
    writing_project = project
    if cross_project:
        writing_project = Project(code="PRECISION-NEXT", name="后续研究项目")
        db.add(writing_project)
    db.flush()
    method = Task(
        project=project, name="方法开发", task_type="test", status="running",
        requires_instrument=True, requires_human=True, assignee=operator,
        est_duration_hours=12,
    )
    writing = Task(
        project=writing_project, name="方案撰写", task_type="manual", status="scheduled",
        requires_instrument=False, requires_human=True, assignee=operator,
        est_duration_hours=0.5,
    )
    db.add_all([method, writing])
    db.flush()
    db.add(TaskDependency(task_id=writing.id, predecessor_id=method.id))
    completed_slot = TimeSlot(
        task=method, instrument_id=instrument.id, status="running", tier="confirmed",
        plan_start=datetime(2026, 9, 9, 9), plan_end=datetime(2026, 9, 9, 20),
        actual_start=datetime(2026, 9, 9, 8, 35, 27),
    )
    future_slot = TimeSlot(
        task=method, instrument_id=instrument.id, status="scheduled", tier="confirmed",
        plan_start=datetime(2026, 9, 10, 8, 30), plan_end=datetime(2026, 9, 10, 9, 30),
    )
    original_writing = TimeSlot(
        task=writing, status="scheduled", tier="confirmed",
        plan_start=datetime(2026, 9, 10, 9, 30), plan_end=datetime(2026, 9, 10, 10),
    )
    db.add_all([completed_slot, future_slot, original_writing])
    db.commit()
    completed_at = datetime(2026, 9, 9, 10, 0, 20, 464087)

    with patch("app.services.scheduler.datetime", FixedDatetime), patch(
        "app.services.scheduler_persistence.datetime", FixedDatetime,
    ):
        result = complete_task_and_shift(db, method.id, actual_end_time=completed_at)

    assert result["status"] == "ok"
    assert result["moved_tasks"] == 1, result["message"]
    assert method.status == "completed"
    assert completed_slot.actual_end == completed_at
    assert future_slot.lifecycle_status == "superseded"
    assert original_writing.lifecycle_status == "superseded"
    new_slot = db.query(TimeSlot).filter(
        TimeSlot.task_id == writing.id, TimeSlot.lifecycle_status == "active",
    ).one()
    assert new_slot.plan_start == datetime(2026, 9, 9, 10, 30)
    assert new_slot.plan_end == datetime(2026, 9, 9, 11)
    assert new_slot.actual_start is None
