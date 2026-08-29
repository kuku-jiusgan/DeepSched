"""待签批工时的预测铺排规则。

签批哪天通过没有依据，这些工时不占具体时间轴位置。但为了让人看出它们大致占到
哪一天，甘特图会在已排工作之后铺出预测块——铺的位置必须站得住脚。
"""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot
from app.services.pending_approval_forecast_service import pending_approval_segments


class PendingApprovalForecastTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        monday = datetime.now() + timedelta(days=1)
        while monday.weekday() != 0:
            monday += timedelta(days=1)
        self.monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        self.instrument = Instrument(
            code="FORECAST-INST", name="预测测试仪器",
            availability_status="available", status="idle",
        )
        self.db.add(self.instrument)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def _build(self, code: str, with_slot: bool):
        project = Project(
            code=code, name=code, estimated_hours=20,
            start_date=self.monday, end_date=self.monday + timedelta(days=30),
        )
        self.db.add(project)
        self.db.flush()
        develop = Task(
            project_id=project.id, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, est_duration_hours=4,
            instrument_ids=[self.instrument.id], status="pending",
        )
        gate = Task(
            project_id=project.id, name="方案签批", task_type="approval_gate",
            requires_instrument=False, status="waiting_external",
            is_external_gate=True, gate_status="not_submitted",
        )
        verify = Task(
            project_id=project.id, name="方法验证", task_type="FFYZ_001",
            requires_instrument=True, est_duration_hours=8,
            instrument_ids=[self.instrument.id], status="waiting_external",
        )
        self.db.add_all([develop, gate, verify])
        self.db.flush()
        self.db.add_all([
            TaskDependency(task_id=gate.id, predecessor_id=develop.id),
            TaskDependency(task_id=verify.id, predecessor_id=gate.id),
        ])
        if with_slot:
            self.db.add(TimeSlot(
                task_id=develop.id, instrument_id=self.instrument.id,
                schedule_run_id="r",
                plan_start=self.monday.replace(hour=8, minute=30),
                plan_end=self.monday.replace(hour=12, minute=30),
                tier="confirmed", status="scheduled", lifecycle_status="active",
            ))
        self.db.flush()
        return project

    def test_unplanned_project_produces_no_forecast(self):
        """连方法开发都没排的项目，谈不上"签批后接着做"。"""
        self._build("NOPLAN", with_slot=False)

        self.assertEqual([], pending_approval_segments(self.db))

    def test_forecast_starts_after_the_project_own_work(self):
        """签批后的活接在本项目前置工作之后，仪器空出来了也不能提前做。"""
        self._build("PLANNED", with_slot=True)

        segments = pending_approval_segments(self.db)

        self.assertTrue(segments)
        self.assertEqual("方法验证", segments[0]["task_name"])
        self.assertGreaterEqual(
            segments[0]["plan_start"], self.monday.replace(hour=12, minute=30),
        )

    def test_forecast_is_split_per_working_day(self):
        """8 小时接在 12:30 之后，当天只剩 7.5 小时，必须跨到次日。"""
        self._build("PLANNED", with_slot=True)

        segments = pending_approval_segments(self.db)

        self.assertGreater(len(segments), 1)
        for segment in segments:
            self.assertEqual(segment["plan_start"].date(), segment["plan_end"].date())
            self.assertLess(segment["plan_start"].weekday(), 5)

    def test_unplanned_project_is_skipped_even_next_to_a_planned_one(self):
        self._build("PLANNED", with_slot=True)
        self._build("NOPLAN", with_slot=False)

        codes = {segment["project_code"] for segment in pending_approval_segments(self.db)}

        self.assertEqual({"PLANNED"}, codes)


if __name__ == "__main__":
    unittest.main()
