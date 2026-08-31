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

    def _build(self, code: str, with_slot: bool, assignee_id: int | None = None, with_report: bool = False):
        project = Project(
            code=code, name=code, estimated_hours=20,
            start_date=self.monday, end_date=self.monday + timedelta(days=30),
        )
        self.db.add(project)
        self.db.flush()
        develop = Task(
            project_id=project.id, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, est_duration_hours=4, assignee_id=assignee_id,
            instrument_ids=[self.instrument.id], status="pending", plan_order=0,
        )
        gate = Task(
            project_id=project.id, name="方案签批", task_type="approval_gate",
            requires_instrument=False, status="waiting_external",
            is_external_gate=True, gate_status="not_submitted",
        )
        verify = Task(
            project_id=project.id, name="方法验证", task_type="FFYZ_001",
            requires_instrument=True, est_duration_hours=8, assignee_id=assignee_id,
            instrument_ids=[self.instrument.id], status="waiting_external", plan_order=2,
        )
        self.db.add_all([develop, gate, verify])
        self.db.flush()
        self.db.add_all([
            TaskDependency(task_id=gate.id, predecessor_id=develop.id),
            TaskDependency(task_id=verify.id, predecessor_id=gate.id),
        ])
        if with_report:
            report = Task(
                project_id=project.id, name="报告撰写", task_type="ZXBG_001",
                requires_instrument=False, est_duration_hours=2, assignee_id=assignee_id,
                status="waiting_external", plan_order=3,
            )
            self.db.add(report)
            self.db.flush()
            self.db.add(TaskDependency(task_id=report.id, predecessor_id=verify.id))
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


class PendingApprovalBridgeRuleTest(PendingApprovalForecastTest):
    """非仪器的下游任务，按仪器排队顺序判定是否构成桥接。

    桥接是跨项目按仪器队列判定的，不是在单个项目的任务链里判定：一个不占仪器的
    任务，只有当这台仪器上它后面还排着同一负责人的仪器任务时，才真的把仪器占住
    ——样品还在机器里，别人插不进来。
    """

    def test_trailing_non_instrument_task_is_not_shown(self):
        self._build("SOLO", with_slot=True, assignee_id=1, with_report=True)

        names = {segment["task_name"] for segment in pending_approval_segments(self.db)}

        self.assertIn("方法验证", names)
        self.assertNotIn("报告撰写", names)

    def test_non_instrument_task_bridges_when_another_project_follows(self):
        self._build("AAA", with_slot=True, assignee_id=1, with_report=True)
        self._build("BBB", with_slot=True, assignee_id=1)

        segments = pending_approval_segments(self.db)
        bridged = [s for s in segments if s["task_name"] == "报告撰写"]

        self.assertEqual(1, len(bridged))
        self.assertEqual("AAA", bridged[0]["project_code"])

    def test_no_bridge_when_the_following_task_has_another_assignee(self):
        self._build("AAA", with_slot=True, assignee_id=1, with_report=True)
        self._build("BBB", with_slot=True, assignee_id=2)

        names = {s["task_name"] for s in pending_approval_segments(self.db)}

        self.assertNotIn("报告撰写", names)


class PendingApprovalBranchBoundTest(unittest.TestCase):
    """预测起点按分支算，不按项目算。

    一个项目常有多条互不相干的并行链（不同负责人、不同仪器各自走完整流程）。
    用项目整体的收工时刻当下界，会让早就干完的那条分支白等最慢的那条——实测
    中一条 09-02 就收工的分支被另一条干到 09-15 的分支拖住，仪器空着好几天。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        monday = datetime.now() + timedelta(days=1)
        while monday.weekday() != 0:
            monday += timedelta(days=1)
        self.monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        # 两条分支各用一台仪器：真实场景里不同负责人的并行链往往在不同仪器上，
        # 这样才能把「项目级下界」的影响和「仪器已被占用」区分开。
        self.instrument = Instrument(code="BRANCH-A", name="分支仪器A",
                                     availability_status="available", status="idle")
        self.other_instrument = Instrument(code="BRANCH-B", name="分支仪器B",
                                           availability_status="available", status="idle")
        self.project = Project(code="MULTI", name="多分支项目",
                               start_date=self.monday, end_date=self.monday + timedelta(days=60))
        self.db.add_all([self.instrument, self.other_instrument, self.project])
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def _branch(self, name: str, assignee_id: int, develop_days: int, instrument=None):
        instrument = instrument or self.instrument
        group = Task(project_id=self.project.id, name=name, task_type="manual", status="pending")
        self.db.add(group)
        self.db.flush()
        develop = Task(project_id=self.project.id, parent_id=group.id, name="方法开发",
                       task_type="FFKF_001", requires_instrument=True, est_duration_hours=4,
                       assignee_id=assignee_id, instrument_ids=[instrument.id],
                       status="pending", plan_order=0)
        gate = Task(project_id=self.project.id, parent_id=group.id, name="方案签批",
                    task_type="approval_gate", status="waiting_external",
                    is_external_gate=True, gate_status="not_submitted", plan_order=1)
        verify = Task(project_id=self.project.id, parent_id=group.id, name="方法验证",
                      task_type="FFYZ_001", requires_instrument=True, est_duration_hours=4,
                      assignee_id=assignee_id, instrument_ids=[instrument.id],
                      status="waiting_external", plan_order=2)
        self.db.add_all([develop, gate, verify])
        self.db.flush()
        self.db.add_all([
            TaskDependency(task_id=gate.id, predecessor_id=develop.id),
            TaskDependency(task_id=verify.id, predecessor_id=gate.id),
        ])
        start = self.monday + timedelta(days=develop_days)
        self.db.add(TimeSlot(
            task_id=develop.id, instrument_id=instrument.id, schedule_run_id="r",
            plan_start=start.replace(hour=8, minute=30), plan_end=start.replace(hour=12, minute=30),
            tier="confirmed", status="scheduled", lifecycle_status="active",
        ))
        self.db.flush()
        return verify

    def test_early_branch_is_not_held_back_by_a_slow_one(self):
        early = self._branch("早分支", assignee_id=1, develop_days=0)
        self._branch("慢分支", assignee_id=2, develop_days=10, instrument=self.other_instrument)
        self.db.commit()

        segments = [s for s in pending_approval_segments(self.db) if s["task_id"] == early.id]

        self.assertTrue(segments)
        # 早分支自己的开发当天 12:30 就完了，不该被慢分支拖到十天后
        self.assertLess(segments[0]["plan_start"], self.monday + timedelta(days=3))
