import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TaskExecutionSegment, TimeSlot, User
from app.services.task_execution_service import start_task_execution
from app.services.task_pause_service import list_switch_candidates, pause_and_switch_task


class TaskPauseServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        self.operator = User(username="tech", display_name="技术员", role="技术员")
        self.instrument = Instrument(code="LCMS-01", name="液质联用仪")
        self.project_a = Project(code="A", name="项目A")
        self.project_b = Project(code="B", name="项目B")
        self.db.add_all([self.operator, self.instrument, self.project_a, self.project_b])
        self.db.flush()
        now = datetime.now()
        self.source_task = Task(
            project_id=self.project_a.id,
            name="方法开发A",
            task_type="FFKF_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.target_task = Task(
            project_id=self.project_b.id,
            name="方法开发B",
            task_type="FFKF_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.db.add_all([self.source_task, self.target_task])
        self.db.flush()
        self.source_slot = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=now - timedelta(hours=1),
            plan_end=now + timedelta(hours=2),
            status="scheduled",
            tier="confirmed",
        )
        self.target_slot = TimeSlot(
            task_id=self.target_task.id,
            instrument_id=self.instrument.id,
            plan_start=now + timedelta(hours=2),
            plan_end=now + timedelta(hours=5),
            status="scheduled",
            tier="confirmed",
        )
        self.db.add_all([self.source_slot, self.target_slot])
        self.db.commit()
        start_task_execution(self.db, self.source_slot.id, self.operator.id)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_pause_releases_instrument_without_completing_task(self):
        result = pause_and_switch_task(
            self.db, self.source_slot.id, "等待样品", self.operator,
        )
        self.db.commit()

        self.assertEqual("ok", result["status"])
        self.assertEqual("paused", self.source_task.status)
        self.assertEqual("paused", self.source_slot.status)
        self.assertIsNotNone(self.source_slot.actual_end)
        segment = self.db.query(TaskExecutionSegment).one()
        self.assertEqual("paused", segment.end_reason)
        self.assertEqual("等待样品", segment.pause_reason)

    def test_pause_and_switch_starts_selected_candidate(self):
        pause_and_switch_task(
            self.db,
            self.source_slot.id,
            "紧急插单",
            self.operator,
            self.target_slot.id,
        )
        self.db.commit()

        self.assertEqual("paused", self.source_task.status)
        self.assertEqual("running", self.target_task.status)
        self.assertIsNotNone(self.target_slot.actual_start)
        self.assertIsNone(self.target_slot.actual_end)
        self.assertEqual(2, self.db.query(TaskExecutionSegment).count())

    def test_candidate_with_paused_predecessor_is_excluded(self):
        dependent_task = Task(
            project_id=self.project_a.id,
            name="方案撰写",
            task_type="QCFA_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.db.add(dependent_task)
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=dependent_task.id,
            predecessor_id=self.source_task.id,
        ))
        self.db.add(TimeSlot(
            task_id=dependent_task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime.now() + timedelta(hours=5),
            plan_end=datetime.now() + timedelta(hours=6),
            status="scheduled",
            tier="confirmed",
        ))
        self.db.commit()

        candidates = list_switch_candidates(self.db, self.source_slot.id)

        self.assertEqual([self.target_task.id], [item["task_id"] for item in candidates])

    def test_paused_task_can_resume_with_new_execution_segment(self):
        pause_and_switch_task(self.db, self.source_slot.id, "等待样品", self.operator)
        self.db.commit()

        start_task_execution(self.db, self.source_slot.id, self.operator.id)
        self.db.commit()

        self.assertEqual("running", self.source_task.status)
        self.assertIsNone(self.source_slot.actual_end)
        self.assertEqual(2, self.db.query(TaskExecutionSegment).count())


if __name__ == "__main__":
    unittest.main()
