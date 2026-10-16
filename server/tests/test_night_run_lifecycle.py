from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.errors import DomainConflictError
from app.models import Instrument, Project, Task, TaskExecutionSegment, TaskNightRun, TimeSlot, User
from app.services.night_run_lifecycle_service import advance_night_runs
from app.services.schedule_night_run_service import record_night_run, ScheduleNightRunInvalidError
from app.services.workspace_service import get_workspace_tasks
from app.services.workspace_night_run_service import night_run_action


@pytest.fixture
def night_context():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        owner = User(id=1, username="analyst", display_name="负责人", role="技术员", is_active=True)
        task = Task(id=1, project_id=1, name="方法验证", task_type="test", status="running", assignee_id=1)
        source = TimeSlot(
            id=1, task_id=1, instrument_id=1, status="running",
            plan_start=datetime(2026, 10, 15, 13, 30), plan_end=datetime(2026, 10, 15, 20),
            actual_start=datetime(2026, 10, 15, 13, 7, 1),
        )
        night = TimeSlot(
            id=2, task_id=1, instrument_id=1, status="scheduled", is_night_run=True,
            plan_start=datetime(2026, 10, 15, 20), plan_end=datetime(2026, 10, 16, 8, 30),
        )
        today = TimeSlot(
            id=3, task_id=1, instrument_id=1, status="scheduled",
            plan_start=datetime(2026, 10, 16, 8, 30), plan_end=datetime(2026, 10, 16, 20),
        )
        db.add_all([
            owner, Project(id=1, code="TEST", name="测试项目"), task,
            Instrument(id=1, code="I-1", name="测试仪器", effective_work_end="20:00"),
            source, night, today,
            TaskExecutionSegment(task_id=1, slot_id=1, instrument_id=1, started_at=source.actual_start),
            TaskNightRun(task_id=1, slot_id=2, instrument_id=1, started_at=night.plan_start, ended_at=night.plan_end),
        ])
        db.commit()
        yield db, task, source, night, today, owner
    engine.dispose()


def test_automatic_boundaries_preserve_task_execution(night_context):
    db, task, source, night, _, _ = night_context
    assert advance_night_runs(db, night.plan_start - timedelta(seconds=1)) == 0
    assert advance_night_runs(db, night.plan_start) == 1
    assert night.status == "running"
    assert advance_night_runs(db, night.plan_end - timedelta(seconds=1)) == 0
    assert advance_night_runs(db, night.plan_end) == 1
    assert night.status == "completed"
    assert advance_night_runs(db, night.plan_end + timedelta(hours=1)) == 0
    assert task.status == source.status == "running"
    assert task.execution_segments[0].ended_at is None
    assert night.actual_start is night.actual_end is None


def test_restart_catches_up_expired_night_and_next_registration_uses_today(night_context):
    db, task, source, night, today, owner = night_context
    now = datetime(2026, 10, 16, 13, 30)
    assert advance_night_runs(db, now) == 1
    output = get_workspace_tasks(db, owner, now)[0]
    assert output.actionable_slot.id == source.id
    assert output.night_run.is_eligible
    assert output.night_run.slot_id == today.id
    assert output.night_run.start_at == today.plan_end
    next_night = record_night_run(db, today.id, 8, "20:00", "次日 08:30", now=now)
    assert next_night.plan_start == datetime(2026, 10, 16, 20)
    assert next_night.plan_end == datetime(2026, 10, 17, 4)
    assert next_night.status == "scheduled"
    assert night.status == "completed"
    assert task.status == "running"


def test_yesterday_source_and_paused_task_cannot_register(night_context):
    db, task, source, _, today, _ = night_context
    now = datetime(2026, 10, 16, 13, 30)
    with pytest.raises(ScheduleNightRunInvalidError, match="当天"):
        record_night_run(db, source.id, 8, "20:00", "次日 08:30", now=now)
    task.status = "paused"
    task.execution_segments[0].ended_at = now
    with pytest.raises(ScheduleNightRunInvalidError, match="进行中"):
        record_night_run(db, today.id, 8, "20:00", "次日 08:30", now=now)


def test_cancelled_night_is_excluded(night_context):
    db, _, _, night, _, _ = night_context
    night.lifecycle_status = "superseded"
    night.status = "cancelled"
    db.flush()
    assert advance_night_runs(db, night.plan_end) == 0
    assert night.status == "cancelled"


def test_mismatched_registration_fails(night_context):
    db, _, _, night, _, _ = night_context
    night.plan_end += timedelta(hours=1)
    with pytest.raises(DomainConflictError, match="不一致"):
        advance_night_runs(db, night.plan_end)


def test_read_status_expires_without_waiting_for_worker(night_context):
    from app.api.schedules import _slot_execution_status

    _, task, _, night, _, _ = night_context
    with patch("app.api.schedules.datetime") as clock:
        clock.now.return_value = night.plan_end
        assert _slot_execution_status(night, task) == "completed"
        night.lifecycle_status = "superseded"
        night.status = "cancelled"
        assert _slot_execution_status(night, task) == "cancelled"


def test_short_today_and_future_slots_remain_ineligible(night_context):
    db, task, source, night, today, _ = night_context
    now = datetime(2026, 10, 16, 13, 30)
    today.plan_end = today.plan_end.replace(hour=19, minute=30)
    action = night_run_action(db, task, [source, night, today], now)
    assert not action.is_eligible
    assert "20:00" in action.reason
    today.plan_start += timedelta(days=1)
    today.plan_end += timedelta(days=1)
    action = night_run_action(db, task, [source, night, today], now)
    assert not action.is_eligible
    assert action.slot_id is None


def test_reregistered_cancelled_night_is_active(night_context):
    db, task, source, night, _, _ = night_context
    night.lifecycle_status = "superseded"
    night.status = "cancelled"
    record = db.query(TaskNightRun).one()
    record.lifecycle_status = "superseded"
    record.superseded_reason = "取消登记"
    db.flush()
    replacement = record_night_run(
        db, source.id, 8, "20:00", "次日 08:30", now=datetime(2026, 10, 15, 19),
    )
    assert replacement.id != night.id
    assert record.lifecycle_status == "active"
    assert record.slot_id == replacement.id
    assert advance_night_runs(db, replacement.plan_end) == 1
    assert replacement.status == "completed"
    assert task.status == "running"
