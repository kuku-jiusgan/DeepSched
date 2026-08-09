import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Task, TaskNightRun, TimeSlot
from app.services.schedule_night_run_service import record_night_run


class ScheduleNightRunTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_update_existing_night_run_can_shorten_end_time(self):
        task = Task(project_id=1, name="night-run", task_type="test", status="scheduled")
        self.db.add(task)
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 13, 8, 30),
            plan_end=datetime(2026, 7, 13, 20, 0),
            status="scheduled",
        )
        self.db.add(slot)
        self.db.commit()

        record_night_run(self.db, slot.id, 8, "20:00", "次日 08:30")
        self.db.refresh(slot)
        self.assertEqual(datetime(2026, 7, 14, 4, 0), slot.plan_end)
        self.assertTrue(slot.is_night_run)
        self.assertEqual(1, self.db.query(TaskNightRun).count())

        record_night_run(self.db, slot.id, 4, "20:00", "次日 08:30")
        self.db.refresh(slot)

        self.assertEqual(datetime(2026, 7, 14, 0, 0), slot.plan_end)
        record = self.db.query(TaskNightRun).one()
        self.assertEqual(datetime(2026, 7, 13, 20, 0), record.started_at)
        self.assertEqual(datetime(2026, 7, 14, 0, 0), record.ended_at)

    def test_task_can_store_multiple_night_runs(self):
        task = Task(project_id=1, name="multi-night", task_type="test", status="scheduled")
        self.db.add(task)
        self.db.flush()
        slots = [
            TimeSlot(
                task_id=task.id,
                instrument_id=1,
                plan_start=datetime(2026, 7, day, 8, 30),
                plan_end=datetime(2026, 7, day, 20, 0),
                status="scheduled",
            )
            for day in (13, 14)
        ]
        self.db.add_all(slots)
        self.db.commit()

        for slot in slots:
            record_night_run(self.db, slot.id, 4, "20:00", "次日 08:30")
        self.db.commit()

        records = self.db.query(TaskNightRun).order_by(TaskNightRun.started_at).all()
        self.assertEqual(2, len(records))
        self.assertEqual(datetime(2026, 7, 13, 20, 0), records[0].started_at)
        self.assertEqual(datetime(2026, 7, 14, 20, 0), records[1].started_at)


if __name__ == "__main__":
    unittest.main()
