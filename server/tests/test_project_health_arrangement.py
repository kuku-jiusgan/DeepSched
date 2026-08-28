import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TaskExecutionSegment, TimeSlot, User
from app.services.project_health_service import get_project_health


class ProjectHealthArrangementTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(id=1, username="analyst", display_name="分析员", role="分析员")
        self.instrument = Instrument(id=1, code="LCMS-01", name="液质联用仪")
        self.project = Project(
            id=1,
            code="P-001",
            name="项目一",
            status="active",
            start_date=datetime(2026, 8, 7),
            end_date=datetime(2026, 8, 13, 23, 59),
        )
        self.scheduled = Task(
            id=1,
            project_id=1,
            name="方法验证",
            task_type="instrument",
            status="done",
            delay_status="not_delayed",
            plan_order=1,
            assignee_id=1,
        )
        self.unscheduled = Task(
            id=2,
            project_id=1,
            name="报告撰写",
            task_type="manual",
            status="pending",
            delay_status="delayed",
            plan_order=2,
            assignee_id=1,
        )
        self.db.add_all([self.user, self.instrument, self.project, self.scheduled, self.unscheduled])
        self.db.add(TimeSlot(
            id=1,
            schedule_run_id="run-1",
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 8, 8, 8, 30),
            plan_end=datetime(2026, 8, 8, 12),
            actual_start=datetime(2026, 8, 8, 8, 45),
            actual_end=datetime(2026, 8, 8, 12, 30),
            status="completed",
            tier="confirmed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_arrangement_items_keep_slot_and_actual_resource_details(self):
        result = get_project_health(self.db, self.project)

        self.assertEqual([1, 2], [item.task_id for item in result.arrangement_items])
        slot_item = result.arrangement_items[0]
        self.assertEqual(1, slot_item.slot_id)
        self.assertEqual("LCMS-01", slot_item.instrument_code)
        self.assertEqual(datetime(2026, 8, 8, 8, 30), slot_item.plan_start)
        self.assertEqual(datetime(2026, 8, 8, 8, 45), slot_item.actual_start)

        unscheduled_item = result.arrangement_items[1]
        self.assertIsNone(unscheduled_item.slot_id)
        self.assertIsNone(unscheduled_item.plan_start)
        self.assertEqual("delayed", unscheduled_item.delay_status)

    def test_arrangement_uses_open_execution_segment_as_running_status(self):
        self.scheduled.status = "scheduled"
        self.db.add(TaskExecutionSegment(
            task_id=self.scheduled.id,
            slot_id=1,
            instrument_id=self.instrument.id,
            operator_id=self.user.id,
            started_at=datetime(2026, 8, 8, 8, 45),
        ))
        self.db.commit()

        result = get_project_health(self.db, self.project)

        self.assertEqual("running", result.arrangement_items[0].task_status)


if __name__ == "__main__":
    unittest.main()


class ProjectHealthPredictedEndTest(unittest.TestCase):
    """未排入排程的工作必须体现在交付预测里，否则延期风险会被掩盖。"""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(id=1, username="analyst", display_name="分析员", role="分析员")
        self.instrument = Instrument(id=1, code="LCMS-01", name="液质联用仪")
        self.project = Project(
            id=1, code="P-002", name="项目二", status="active",
            estimated_hours=100,
            start_date=datetime(2026, 8, 3),
            end_date=datetime(2026, 8, 14, 23, 59),
        )
        self.writing = Task(
            id=1, project_id=1, name="方案撰写", task_type="QCFA_001",
            est_duration_hours=4, status="scheduled", plan_order=1, assignee_id=1,
        )
        self.gate = Task(
            id=2, project_id=1, name="方案签批", task_type="approval_gate",
            is_external_gate=True, gate_status="not_submitted",
            status="waiting_external", plan_order=2,
        )
        self.validation = Task(
            id=3, project_id=1, name="方法验证", task_type="FFYZ_001",
            requires_instrument=True, est_duration_hours=24,
            status="waiting_external", plan_order=3, assignee_id=1,
        )
        self.db.add_all([
            self.user, self.instrument, self.project,
            self.writing, self.gate, self.validation,
        ])
        self.db.add_all([
            TaskDependency(task_id=2, predecessor_id=1),
            TaskDependency(task_id=3, predecessor_id=2),
        ])
        self.db.add(TimeSlot(
            id=1, schedule_run_id="run-1", task_id=1, instrument_id=1,
            plan_start=datetime(2026, 8, 4, 9, 0),
            plan_end=datetime(2026, 8, 4, 13, 0),
            status="scheduled", tier="confirmed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_unscheduled_post_approval_work_extends_predicted_end(self):
        result = get_project_health(self.db, self.project)

        # 项目只有一个 8/4 结束的时间槽，但方法验证还有 24 小时没排进去，
        # 预测完工必须落在时间槽之后。
        self.assertGreater(result.summary.predicted_end, datetime(2026, 8, 4, 13, 0))

    def test_post_approval_work_beyond_due_date_marks_overdue(self):
        self.validation.est_duration_hours = 400
        self.db.commit()

        result = get_project_health(self.db, self.project)

        self.assertGreater(result.summary.predicted_end, self.project.end_date)
        self.assertEqual("overdue", result.summary.delivery_status)

    def test_approved_gate_leaves_prediction_on_slots(self):
        self.gate.gate_status = "approved"
        self.gate.approved_at = datetime(2026, 8, 5, 9, 0)
        self.db.commit()

        result = get_project_health(self.db, self.project)

        self.assertEqual(datetime(2026, 8, 4, 13, 0), result.summary.predicted_end)
