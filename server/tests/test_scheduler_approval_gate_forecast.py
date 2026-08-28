import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot
from app.services.scheduler import SchedulerService


class SchedulerApprovalGateForecastTest(unittest.TestCase):
    """方案签批下游任务：计入产能测算，但签批前不落地时间槽。"""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        # 锚定到下一个周一：周末不排产，否则产能会随运行日期漂移。
        tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=8, minute=30, second=0, microsecond=0,
        )
        self.horizon_start = tomorrow + timedelta(
            days=(7 - tomorrow.weekday()) % 7,
        )

    def tearDown(self):
        self.db.close()

    def _build_plan(self, deadline):
        project = Project(
            name="签批下游产能测算", code=f"GATE-FORECAST-{deadline:%d%H%M}",
            estimated_hours=12, start_date=self.horizon_start, end_date=deadline,
        )
        instrument = Instrument(
            code="GATE-INST", name="签批测试仪器",
            availability_status="available", status="idle",
        )
        self.db.add_all([project, instrument])
        self.db.flush()
        develop = Task(
            project_id=project.id, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, requires_human=False, est_duration_hours=4,
            instrument_ids=[instrument.id], status="pending",
        )
        gate = Task(
            project_id=project.id, name="方案签批", task_type="approval_gate",
            requires_instrument=False, requires_human=False, est_duration_hours=None,
            status="waiting_external", is_external_gate=True, gate_status="not_submitted",
        )
        verify = Task(
            project_id=project.id, name="方法验证", task_type="FFYZ_001",
            requires_instrument=True, requires_human=False, est_duration_hours=8,
            instrument_ids=[instrument.id], status="waiting_external",
        )
        self.db.add_all([develop, gate, verify])
        self.db.flush()
        self.db.add_all([
            TaskDependency(task_id=gate.id, predecessor_id=develop.id),
            TaskDependency(task_id=verify.id, predecessor_id=gate.id),
        ])
        self.db.flush()
        return project, develop, verify

    def _generate(self, project):
        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(
                self.horizon_start, self.horizon_start + timedelta(days=14), 14 * 48,
            ),
        ):
            return SchedulerService(self.db).generate(
                project_ids=[project.id], current_project_id=project.id, commit=False,
            )

    def test_unapproved_downstream_hours_block_an_unreachable_deadline(self):
        # 截止日期只放得下方法开发，放不下签批后的方法验证。这段工时若不计入
        # 求解，排程会假装按期完成，等签批通过才暴露延期。
        project, _develop, _verify = self._build_plan(
            self.horizon_start + timedelta(hours=5),
        )

        result = self._generate(project)

        self.assertNotEqual("ok", result["status"])

    def test_downstream_occupies_capacity_without_persisting_slots(self):
        project, develop, verify = self._build_plan(
            self.horizon_start + timedelta(days=4),
        )

        result = self._generate(project)

        self.assertEqual("ok", result["status"])
        self.assertTrue(
            self.db.query(TimeSlot).filter(TimeSlot.task_id == develop.id).count(),
        )
        # 签批前不生成后续排程：方法验证参与了求解，但不落地时间槽，状态不变。
        self.assertEqual(
            0, self.db.query(TimeSlot).filter(TimeSlot.task_id == verify.id).count(),
        )
        self.assertEqual("waiting_external", verify.status)


if __name__ == "__main__":
    unittest.main()
