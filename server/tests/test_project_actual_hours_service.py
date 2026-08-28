import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskExecutionSegment, TaskNightRun, TimeSlot
from app.services.project_actual_hours_service import project_actual_hours_map, task_actual_hours_map


class ProjectActualHoursServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.project = Project(code="P-001", name="测试项目")
        self.instrument = Instrument(code="I-001", name="检测仪", availability_status="available")
        self.db.add_all([self.project, self.instrument])
        self.db.flush()
        self.task = Task(project_id=self.project.id, name="检测", task_type="RCJC_001", status="done")
        self.db.add(self.task)
        self.db.flush()
        self.slot = TimeSlot(
            task_id=self.task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 7, 31, 19, 0),
            plan_end=datetime(2026, 7, 31, 22, 0),
            actual_start=datetime(2026, 7, 31, 19, 0),
            actual_end=datetime(2026, 7, 31, 22, 0),
            status="completed",
        )
        self.db.add(self.slot)
        self.db.flush()
        self.db.add(TaskExecutionSegment(
            task_id=self.task.id,
            slot_id=self.slot.id,
            started_at=datetime(2026, 7, 31, 19, 0),
            ended_at=datetime(2026, 7, 31, 22, 0),
            end_reason="completed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_excludes_non_working_hours_without_night_run(self):
        self.assertEqual(1.0, project_actual_hours_map(self.db, [self.project])[self.project.id])
        self.assertEqual(1.0, task_actual_hours_map(self.db, [self.task.id])[self.task.id])

    def test_adds_registered_night_run_hours(self):
        self.db.add(TaskNightRun(
            task_id=self.task.id,
            slot_id=self.slot.id,
            instrument_id=self.instrument.id,
            started_at=datetime(2026, 7, 31, 20, 0),
            ended_at=datetime(2026, 7, 31, 22, 0),
        ))
        self.db.commit()

        self.assertEqual(3.0, project_actual_hours_map(self.db, [self.project])[self.project.id])

    def test_does_not_add_slot_time_when_execution_segment_exists(self):
        self.slot.actual_start = datetime(2026, 7, 31, 19, 0)
        self.slot.actual_end = datetime(2026, 7, 31, 22, 0)
        self.db.commit()

        self.assertEqual(1.0, task_actual_hours_map(self.db, [self.task.id])[self.task.id])


if __name__ == "__main__":
    unittest.main()
