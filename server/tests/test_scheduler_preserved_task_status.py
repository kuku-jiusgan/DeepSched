import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.scheduler import SchedulerService


class SchedulerPreservedTaskStatusTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.horizon_start = datetime.now().replace(hour=8, minute=30, second=0, microsecond=0)

    def tearDown(self):
        self.db.close()

    def test_explicit_paused_task_persists_future_slots_without_changing_status(self):
        project = Project(
            name="暂停任务重排", code="PAUSED-REPLAN",
            start_date=self.horizon_start,
            end_date=self.horizon_start + timedelta(days=5),
        )
        instrument = Instrument(
            code="PAUSED-REPLAN-INST", name="暂停重排仪器",
            availability_status="available", status="idle",
        )
        self.db.add_all([project, instrument])
        self.db.flush()
        task = Task(
            project_id=project.id, name="暂停源任务", task_type="test",
            requires_instrument=True, requires_human=False,
            status="paused", est_duration_hours=1,
            instrument_ids=[instrument.id],
        )
        self.db.add(task)
        self.db.flush()

        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(
                self.horizon_start,
                self.horizon_start + timedelta(days=10),
                10 * 48,
            ),
        ):
            result = SchedulerService(self.db).generate(
                task_ids=[task.id], current_project_id=project.id, commit=False,
                remaining_duration_minutes={task.id: 60},
                replaceable_task_ids={task.id},
                preserved_status_task_ids={task.id},
            )

        slots = self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).all()
        self.assertEqual("ok", result["status"], result)
        self.assertEqual("paused", task.status)
        self.assertEqual(1, len(slots))
        self.assertEqual("paused", slots[0].status)
        self.assertIsNone(slots[0].actual_start)


if __name__ == "__main__":
    unittest.main()
