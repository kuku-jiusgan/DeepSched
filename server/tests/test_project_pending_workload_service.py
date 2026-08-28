import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TaskDependency, TimeSlot
from app.services.project_pending_workload_service import (
    PendingWorkload,
    pending_approval_workload,
)
from app.services.project_plan_impact_service import project_completions


class ProjectPendingWorkloadServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.project = Project(
            id=1, code="P-001", name="项目一", status="active",
            estimated_hours=100,
            start_date=datetime(2026, 8, 1), end_date=datetime(2026, 9, 30, 23, 59),
        )
        self.writing = Task(
            id=1, project_id=1, name="方案撰写", task_type="QCFA_001",
            est_duration_hours=5, status="scheduled",
        )
        self.db.add_all([self.project, self.writing])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _gate(self, gate_status="not_submitted", expected_approval_at=None):
        gate = Task(
            id=2, project_id=1, name="方案签批", task_type="approval_gate",
            is_external_gate=True, gate_status=gate_status,
            expected_approval_at=expected_approval_at, status="waiting_external",
        )
        self.db.add(gate)
        self.db.add(TaskDependency(task_id=2, predecessor_id=1))
        self.db.commit()
        return gate

    def _downstream(self):
        validation = Task(
            id=3, project_id=1, name="方法验证", task_type="FFYZ_001",
            requires_instrument=True, est_duration_hours=20, status="waiting_external",
        )
        report = Task(
            id=4, project_id=1, name="报告撰写", task_type="ZXBG_001",
            est_duration_hours=5, status="waiting_external",
        )
        self.db.add_all([validation, report])
        self.db.add_all([
            TaskDependency(task_id=3, predecessor_id=2),
            TaskDependency(task_id=4, predecessor_id=3),
        ])
        self.db.commit()
        return validation, report

    def test_existing_downstream_tasks_without_slots_are_counted(self):
        self._gate()
        self._downstream()

        workload = pending_approval_workload(self.db, {1})[1]

        self.assertEqual("tasks", workload.source)
        self.assertEqual(25.0, workload.hours)
        self.assertIsNone(workload.gate_expected_at)
        self.assertFalse(workload.has_expected_approval)

    def test_expected_approval_time_is_reported(self):
        self._gate(gate_status="waiting_approval", expected_approval_at=datetime(2026, 9, 1, 9, 0))
        self._downstream()

        workload = pending_approval_workload(self.db, {1})[1]

        self.assertEqual(datetime(2026, 9, 1, 9, 0), workload.gate_expected_at)
        self.assertTrue(workload.has_expected_approval)

    def test_missing_downstream_tasks_fall_back_to_template_ratio(self):
        self._gate()

        workload = pending_approval_workload(self.db, {1})[1]

        # 方法验证 20% + 报告撰写 5%，项目预计 100 小时。
        self.assertEqual("template", workload.source)
        self.assertEqual(25.0, workload.hours)

    def test_scheduled_downstream_tasks_are_not_counted(self):
        self._gate()
        validation, report = self._downstream()
        for task in (validation, report):
            self.db.add(TimeSlot(
                schedule_run_id="run-1", task_id=task.id,
                plan_start=datetime(2026, 9, 2, 9, 0), plan_end=datetime(2026, 9, 2, 17, 0),
                status="scheduled", tier="forecast",
            ))
        self.db.commit()

        workload = pending_approval_workload(self.db, {1})[1]

        self.assertEqual("tasks", workload.source)
        self.assertEqual(0.0, workload.hours)

    def test_superseded_slot_does_not_count_as_scheduled(self):
        self._gate()
        validation, _report = self._downstream()
        self.db.add(TimeSlot(
            schedule_run_id="run-1", task_id=validation.id,
            plan_start=datetime(2026, 9, 2, 9, 0), plan_end=datetime(2026, 9, 2, 17, 0),
            status="scheduled", tier="forecast", lifecycle_status="superseded",
        ))
        self.db.commit()

        self.assertEqual(25.0, pending_approval_workload(self.db, {1})[1].hours)

    def test_completed_downstream_tasks_are_ignored(self):
        self._gate()
        validation, _report = self._downstream()
        validation.status = "completed"
        self.db.commit()

        self.assertEqual(5.0, pending_approval_workload(self.db, {1})[1].hours)

    def test_approved_gate_produces_no_pending_workload(self):
        self._gate(gate_status="approved")
        self._downstream()

        workload = pending_approval_workload(self.db, {1})[1]

        self.assertEqual("none", workload.source)
        self.assertEqual(0.0, workload.hours)

    def test_executed_minutes_reduce_remaining_hours(self):
        self._gate()
        validation, _report = self._downstream()
        validation.executed_minutes = 600
        self.db.commit()

        self.assertEqual(15.0, pending_approval_workload(self.db, {1})[1].hours)


class ProjectCompletionsPendingTailTest(unittest.TestCase):
    """被顺延项目的完工时间必须把签批后未排的工时算进去。"""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.project = Project(
            id=1, code="P-001", name="项目一", status="active", estimated_hours=100,
            start_date=datetime(2026, 8, 3), end_date=datetime(2026, 8, 14, 23, 59),
        )
        self.task = Task(
            id=1, project_id=1, name="方案撰写", task_type="QCFA_001",
            est_duration_hours=4, status="scheduled",
        )
        self.db.add_all([self.project, self.task])
        self.db.add(TimeSlot(
            id=1, schedule_run_id="run-1", task_id=1,
            plan_start=datetime(2026, 8, 4, 9, 0), plan_end=datetime(2026, 8, 4, 13, 0),
            status="scheduled", tier="confirmed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_completion_without_workload_is_last_slot_end(self):
        completions = project_completions(self.db, {1})

        self.assertEqual(datetime(2026, 8, 4, 13, 0), completions[1])

    def test_pending_workload_pushes_completion_past_last_slot(self):
        completions = project_completions(
            self.db, {1}, {1: PendingWorkload(hours=24.0, source="tasks")},
        )

        self.assertGreater(completions[1], datetime(2026, 8, 4, 13, 0))

    def test_zero_workload_leaves_completion_unchanged(self):
        completions = project_completions(
            self.db, {1}, {1: PendingWorkload(hours=0.0, source="tasks")},
        )

        self.assertEqual(datetime(2026, 8, 4, 13, 0), completions[1])


if __name__ == "__main__":
    unittest.main()
