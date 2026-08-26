import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot
from app.services.scheduler_fixed_slots import load_fixed_slots


class SchedulerFixedSlotsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_ignores_superseded_slots_when_loading_fixed_capacity(self):
        project = Project(name="固定槽项目", code="FIXED-SLOT")
        task = Task(project=project, name="历史任务", task_type="test")
        self.db.add_all([project, task])
        self.db.flush()
        start = datetime(2026, 8, 26, 8, 30)
        active_slot = TimeSlot(
            task_id=task.id, plan_start=start, plan_end=start + timedelta(hours=1),
            status="scheduled",
        )
        superseded_slot = TimeSlot(
            task_id=task.id, plan_start=start + timedelta(hours=1),
            plan_end=start + timedelta(hours=2), status="scheduled",
            lifecycle_status="superseded",
        )
        self.db.add_all([active_slot, superseded_slot])
        self.db.flush()

        slots = load_fixed_slots(self.db)

        self.assertEqual([active_slot.id], [slot.id for slot in slots])


if __name__ == "__main__":
    unittest.main()
