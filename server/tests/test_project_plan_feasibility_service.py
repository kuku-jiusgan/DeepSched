import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot
from app.services.project_plan_errors import ProjectPlanInvalidError

from app.services.project_plan_feasibility_service import (
    validate_immediate_approval_feasibility,
)


class ProjectPlanFeasibilityServiceTest(unittest.TestCase):
    def test_detection_task_does_not_run_immediate_approval_probe(self):
        db = MagicMock()
        project = SimpleNamespace(project_kind="detection")
        task = SimpleNamespace(id=1, project_id=project.id if hasattr(project, "id") else 1)

        validate_immediate_approval_feasibility(
            db,
            project=project,
            replan_tasks=[task],
            released_slot_ids=set(),
        )

        db.query.assert_not_called()


class ImmediateApprovalScopeTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.start = datetime(2026, 9, 9, 8, 30)
        self.instrument = Instrument(code="SCOPE-I", name="排程范围测试仪器")
        self.project = Project(
            code="SCOPE-P", name="当前项目", status="active",
            start_date=self.start, end_date=self.start.replace(hour=20, minute=0),
        )
        self.other = Project(
            code="SCOPE-OTHER", name="已逾期的无关项目", status="active",
            start_date=self.start - timedelta(days=2),
            end_date=self.start - timedelta(days=1),
        )
        self.db.add_all([self.instrument, self.project, self.other])
        self.db.flush()
        self.task = self._create_downstream(self.project)
        self.other_task = self._create_downstream(self.other)
        self.db.commit()
        clock = patch("app.services.scheduler.datetime")
        self.addCleanup(clock.stop)
        clock.start().now.return_value = self.start

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create_downstream(self, project):
        gate = Task(
            project=project, name="方案签批", task_type="approval_gate",
            is_external_gate=True, gate_status="not_submitted",
            status="waiting_external", requires_human=False,
        )
        task = Task(
            project=project, name="方法验证", task_type="test",
            est_duration_hours=2, status="waiting_external",
            requires_human=False, requires_instrument=True,
            instrument_ids=[self.instrument.id],
        )
        self.db.add_all([gate, task])
        self.db.flush()
        self.db.add(TaskDependency(task_id=task.id, predecessor_id=gate.id))
        return task

    def _validate(self, tasks):
        validate_immediate_approval_feasibility(
            self.db, project=self.project, replan_tasks=tasks,
            released_slot_ids=set(),
        )

    def test_unrelated_overdue_approval_does_not_block_current_project(self):
        self._validate([self.task])

        self.assertEqual(self.db.query(TimeSlot).count(), 0)
        self.assertEqual(self.task.status, "waiting_external")
        self.assertEqual(self.other_task.status, "waiting_external")

    def test_project_in_replan_still_constrains_immediate_approval(self):
        with self.assertRaises(ProjectPlanInvalidError):
            self._validate([self.task, self.other_task])

    def test_existing_other_project_instrument_occupancy_is_retained(self):
        occupied = Task(
            project=self.other, name="已排仪器任务", task_type="test",
            status="scheduled", est_duration_hours=11.5,
            requires_instrument=True, requires_human=False,
            instrument_ids=[self.instrument.id],
        )
        self.db.add(occupied)
        self.db.flush()
        self.db.add(TimeSlot(
            task=occupied, instrument_id=self.instrument.id,
            plan_start=self.start, plan_end=self.project.end_date,
            tier="frozen", status="scheduled",
        ))
        self.db.commit()

        with self.assertRaises(ProjectPlanInvalidError):
            self._validate([self.task])


if __name__ == "__main__":
    unittest.main()
