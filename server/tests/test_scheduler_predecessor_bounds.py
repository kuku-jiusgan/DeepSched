import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot
from app.services.scheduler_predecessor_bounds import load_missing_predecessor_ends


class SchedulerPredecessorBoundsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_uses_actual_end_and_ignores_superseded_future_slot(self):
        project = Project(
            code="PRED-001", name="前置任务测试",
            start_date=datetime(2026, 8, 25, 8, 30),
            end_date=datetime(2026, 9, 5, 18, 0),
        )
        task = Task(
            project=project, name="已完成前置任务", task_type="test",
            requires_instrument=False, requires_human=False, status="completed",
        )
        self.db.add(task)
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=task.id,
                plan_start=datetime(2026, 8, 25, 9, 0),
                plan_end=datetime(2026, 8, 25, 10, 0),
                actual_start=datetime(2026, 8, 25, 9, 0),
                actual_end=datetime(2026, 8, 25, 13, 30),
                status="completed",
                lifecycle_status="active",
            ),
            TimeSlot(
                task_id=task.id,
                plan_start=datetime(2026, 8, 31, 14, 30),
                plan_end=datetime(2026, 8, 31, 15, 30),
                status="cancelled",
                lifecycle_status="superseded",
            ),
        ])
        self.db.commit()

        bounds = load_missing_predecessor_ends(
            self.db, {task.id}, datetime(2026, 8, 25, 8, 30),
        )

        self.assertEqual(10, bounds[task.id])


if __name__ == "__main__":
    unittest.main()
