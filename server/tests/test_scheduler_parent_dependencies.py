import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    Instrument,
    Project,
    Task,
    TaskDependency,
    TimeSlot,
)
from app.services.scheduler import SchedulerService


class SchedulerParentDependenciesTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.horizon_start = datetime(2026, 8, 3, 8, 30)

    def tearDown(self):
        self.db.close()

    def test_parent_dependency_waits_for_all_leaf_children(self):
        project = Project(
            name="父子依赖测试",
            code="PARENT-DEPENDENCY",
            estimated_hours=10,
            start_date=self.horizon_start,
            end_date=self.horizon_start + timedelta(days=10),
        )
        lcms = Instrument(
            code="PARENT-LCMS",
            name="LCMS测试仪器",
            availability_status="available",
            status="idle",
        )
        gcms = Instrument(
            code="PARENT-GCMS",
            name="GCMS测试仪器",
            availability_status="available",
            status="idle",
        )
        self.db.add_all([project, lcms, gcms])
        self.db.flush()
        parent = Task(
            project_id=project.id,
            name="方法开发",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=5,
            instrument_ids=[lcms.id, gcms.id],
            status="pending",
        )
        self.db.add(parent)
        self.db.flush()
        lcms_task = Task(
            project_id=project.id,
            parent_id=parent.id,
            name="LCMS方法开发",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=2,
            instrument_ids=[lcms.id],
            status="pending",
        )
        gcms_task = Task(
            project_id=project.id,
            parent_id=parent.id,
            name="GCMS方法开发",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=3,
            instrument_ids=[gcms.id],
            status="pending",
        )
        writing_task = Task(
            project_id=project.id,
            name="方案撰写",
            task_type="test",
            requires_instrument=False,
            requires_human=False,
            est_duration_hours=1,
            status="pending",
        )
        self.db.add_all([lcms_task, gcms_task, writing_task])
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=writing_task.id,
            predecessor_id=parent.id,
        ))
        self.db.flush()

        total_units = 15 * 48
        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(
                self.horizon_start,
                self.horizon_start + timedelta(days=15),
                total_units,
            ),
        ):
            result = SchedulerService(self.db).generate(
                project_ids=[project.id],
                commit=False,
                current_project_id=project.id,
            )

        child_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id.in_([lcms_task.id, gcms_task.id]),
        ).all()
        writing_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == writing_task.id,
        ).order_by(TimeSlot.plan_start).all()
        parent_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == parent.id,
        ).all()
        self.assertEqual("ok", result["status"])
        self.assertEqual([], parent_slots)
        self.assertGreaterEqual(
            writing_slots[0].plan_start,
            max(slot.plan_end for slot in child_slots),
        )


if __name__ == "__main__":
    unittest.main()
