import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.scheduler import SchedulerService


class SchedulerDurationRoundingTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.horizon_start = (datetime.now() + timedelta(days=1)).replace(
            hour=8, minute=30, second=0, microsecond=0,
        )

    def tearDown(self):
        self.db.close()

    def test_fractional_duration_is_never_shorter_than_declared(self):
        project = Project(
            name="小数工时测试",
            code="FRACTIONAL-DURATION",
            estimated_hours=10,
            start_date=self.horizon_start,
            end_date=self.horizon_start + timedelta(days=5),
        )
        instrument = Instrument(
            code="FRACTIONAL-INST",
            name="小数工时仪器",
            availability_status="available",
            status="idle",
        )
        self.db.add_all([project, instrument])
        self.db.flush()
        task = Task(
            project_id=project.id,
            name="45分钟任务",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=0.75,
            instrument_ids=[instrument.id],
            status="pending",
        )
        self.db.add(task)
        self.db.flush()

        total_units = 10 * 48
        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(
                self.horizon_start,
                self.horizon_start + timedelta(days=10),
                total_units,
            ),
        ):
            result = SchedulerService(self.db).generate(
                project_ids=[project.id],
                current_project_id=project.id,
                commit=False,
            )

        slot = self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).one()
        scheduled_minutes = int(
            (slot.plan_end - slot.plan_start).total_seconds() / 60
        )
        self.assertEqual("ok", result["status"])
        self.assertEqual(60, scheduled_minutes)
        self.assertGreaterEqual(scheduled_minutes, 45)


if __name__ == "__main__":
    unittest.main()
