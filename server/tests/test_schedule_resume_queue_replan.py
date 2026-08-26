import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    AuditLog,
    Instrument,
    Project,
    Task,
    TaskExecutionSegment,
    TimeSlot,
    User,
)
from app.services.schedule_completion_service import complete_task_and_shift


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 8, 25, 13, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class ScheduleResumeQueueReplanTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()

    def tearDown(self):
        self.db.close()

    def test_early_completion_resumes_source_and_replans_following_queue(self):
        operator = User(username="tech", display_name="技术员", role="技术员")
        instrument = Instrument(code="LCMS-01", name="液质联用仪")
        source_project = Project(code="DETECT", name="样品检测")
        target_project = Project(code="RESEARCH", name="研究项目")
        following_project = Project(code="NEXT", name="后续项目")
        self.db.add_all([
            operator, instrument, source_project, target_project, following_project,
        ])
        self.db.flush()

        source = Task(
            project=source_project, name="样品检测", task_type="test",
            status="paused", est_duration_hours=1, requires_instrument=True,
            requires_human=True, assignee=operator,
        )
        target = Task(
            project=target_project, name="方法开发", task_type="test",
            status="running", requires_instrument=True, requires_human=True,
            assignee=operator,
        )
        following = Task(
            project=following_project, name="后续方法开发", task_type="test",
            status="scheduled", est_duration_hours=1.75, requires_instrument=True,
            requires_human=True, assignee=operator,
        )
        self.db.add_all([source, target, following])
        self.db.flush()

        old_source_slot = TimeSlot(
            task=source, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 25, 12, 0),
            plan_end=datetime(2026, 8, 25, 12, 30),
            actual_start=datetime(2026, 8, 25, 12, 0),
            actual_end=datetime(2026, 8, 25, 12, 30), status="paused",
        )
        recovery_slot = TimeSlot(
            task=source, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 27, 9, 30),
            plan_end=datetime(2026, 8, 27, 10, 30), status="paused",
        )
        target_slot = TimeSlot(
            task=target, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 25, 12, 30),
            plan_end=datetime(2026, 8, 27, 9, 30),
            actual_start=datetime(2026, 8, 25, 12, 30), status="running",
        )
        following_slot = TimeSlot(
            task=following, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 27, 10, 30),
            plan_end=datetime(2026, 8, 27, 12, 30), status="scheduled",
        )
        stale_slot = TimeSlot(
            task=following, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 28, 8, 30),
            plan_end=datetime(2026, 8, 28, 18, 30), status="scheduled",
            lifecycle_status="superseded", superseded_reason="历史重排",
        )
        self.db.add_all([
            old_source_slot, recovery_slot, target_slot, following_slot, stale_slot,
        ])
        self.db.flush()
        self.db.add_all([
            TaskExecutionSegment(
                task_id=target.id, slot_id=target_slot.id,
                instrument_id=instrument.id,
                started_at=datetime(2026, 8, 25, 12, 30),
            ),
            AuditLog(
                user_name="技术员", action="task_paused", target_type="task",
                target_id=source.id,
                detail={
                    "source_task_id": source.id,
                    "source_slot_id": old_source_slot.id,
                    "target_task_id": target.id,
                    "target_slot_id": target_slot.id,
                },
            ),
        ])
        self.db.commit()

        working_options = {
            "day_start_minutes": 0,
            "day_end_minutes": 24 * 60,
            "include_weekends": True,
            "include_holidays": True,
            "horizon_end": datetime(2026, 9, 30),
            "calendar_days": {},
        }
        with patch("app.services.task_execution_service.datetime", FixedDatetime), patch(
            "app.services.schedule_completion_service._load_working_options",
            return_value=working_options,
        ):
            result = complete_task_and_shift(
                self.db, target.id,
                actual_end_time=FixedDatetime.now(),
                completed_slot_id=target_slot.id,
                release_instrument=True,
            )
        self.db.flush()

        active_following = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following.id,
            TimeSlot.lifecycle_status == "active",
        ).one()
        self.assertEqual(source.id, result["resumed_task_id"])
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(FixedDatetime.now(), recovery_slot.plan_start)
        self.assertEqual(
            recovery_slot.plan_end + timedelta(minutes=30),
            active_following.plan_start,
        )
        self.assertEqual(
            105,
            int((active_following.plan_end - active_following.plan_start).total_seconds() / 60),
        )
        self.assertEqual("superseded", following_slot.lifecycle_status)
        self.assertEqual("superseded", stale_slot.lifecycle_status)


if __name__ == "__main__":
    unittest.main()
