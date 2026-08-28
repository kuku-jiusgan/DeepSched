import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot
from app.services.scheduler_data import (
    load_bridge_candidate_tasks,
    load_diagnostic_resource_tasks,
    load_task_children,
)
from app.services.scheduler_diagnostics import _project_instrument_intervals
from app.services.scheduler_helpers import build_compatibility, build_dependencies


class DiagnosticResourceTasksTest(unittest.TestCase):
    """占用分析必须把"签批后才排程"的仪器任务算进预测工时。

    这类任务还没有时间槽，如果只按"有没有时间槽"筛选，其他项目的预测工时
    会恒为 0，排程失败时看不出真正的仪器缺口。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.instrument = Instrument(id=10, code="测试仪器", name="CSYQ")
        self.current = Project(
            id=1, code="P-CUR", name="当前项目", status="active",
            start_date=datetime(2026, 9, 1), end_date=datetime(2026, 9, 30, 23, 59),
        )
        self.other = Project(
            id=2, code="P-OTHER", name="占用项目", status="active",
            start_date=datetime(2026, 9, 1), end_date=datetime(2026, 9, 30, 23, 59),
        )
        self.development = Task(
            id=20, project_id=2, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, est_duration_hours=35, instrument_ids=[10],
            status="running",
        )
        self.validation = Task(
            id=21, project_id=2, name="方法验证", task_type="FFYZ_001",
            requires_instrument=True, est_duration_hours=10, instrument_ids=[10],
            status="waiting_external",
        )
        self.report = Task(
            id=22, project_id=2, name="报告撰写", task_type="ZXBG_001",
            requires_instrument=False, est_duration_hours=2.5,
            status="waiting_external",
        )
        self.db.add_all([
            self.instrument, self.current, self.other,
            self.development, self.validation, self.report,
        ])
        self.db.add(TimeSlot(
            id=1, schedule_run_id="run-1", task_id=20, instrument_id=10,
            plan_start=datetime(2026, 9, 2, 9, 0), plan_end=datetime(2026, 9, 2, 17, 0),
            status="scheduled", tier="confirmed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_other_project_post_approval_task_enters_diagnostics(self):
        tasks = load_diagnostic_resource_tasks(self.db, set(), current_project_id=1)

        self.assertIn(self.validation.id, {task.id for task in tasks})

    def test_task_without_instrument_stays_out_of_instrument_diagnostics(self):
        tasks = load_diagnostic_resource_tasks(self.db, set(), current_project_id=1)

        # 报告撰写不占仪器，不应出现在仪器占用分析里。
        self.assertNotIn(self.report.id, {task.id for task in tasks})

    def test_post_approval_hours_are_reported_as_forecast(self):
        tasks = [
            task for task in load_diagnostic_resource_tasks(self.db, set(), current_project_id=1)
            if task.project_id == self.other.id
        ]
        compat = build_compatibility(tasks, [self.instrument], True)

        _intervals, breakdown = _project_instrument_intervals(
            tasks, self.instrument.id, compat,
            datetime(2026, 9, 1), self.other.end_date,
        )

        self.assertEqual(8.0, breakdown["slot"])
        self.assertEqual(10.0, breakdown["forecast"])

    def test_completed_task_is_still_excluded(self):
        self.validation.status = "completed"
        self.db.commit()

        tasks = load_diagnostic_resource_tasks(self.db, set(), current_project_id=1)

        self.assertNotIn(self.validation.id, {task.id for task in tasks})


class BridgedInstrumentOccupancyTest(unittest.TestCase):
    """非仪器任务夹在两个同仪器同负责人任务之间时，期间仪器不会被释放。"""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.instrument = Instrument(id=10, code="测试仪器", name="CSYQ")
        self.project = Project(
            id=2, code="P-OTHER", name="占用项目", status="active",
            start_date=datetime(2026, 9, 1), end_date=datetime(2026, 9, 30, 23, 59),
        )
        self.group = Task(
            id=30, project_id=2, name="标准计划1", task_type="group",
            requires_instrument=False, requires_human=False, est_duration_hours=50,
            status="pending",
        )
        self.development = Task(
            id=31, project_id=2, parent_id=30, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, requires_human=True, assignee_id=1,
            est_duration_hours=35, instrument_ids=[10], status="running",
        )
        self.writing = Task(
            id=32, project_id=2, parent_id=30, name="方案撰写", task_type="QCFA_001",
            requires_instrument=False, requires_human=True, assignee_id=1,
            est_duration_hours=2.5, status="scheduled",
        )
        self.gate = Task(
            id=33, project_id=2, parent_id=30, name="方案签批", task_type="approval_gate",
            requires_instrument=False, requires_human=False, is_external_gate=True,
            gate_status="not_submitted", status="waiting_external",
        )
        self.validation = Task(
            id=34, project_id=2, parent_id=30, name="方法验证", task_type="FFYZ_001",
            requires_instrument=True, requires_human=True, assignee_id=1,
            est_duration_hours=10, instrument_ids=[10], status="waiting_external",
        )
        self.db.add_all([
            self.instrument, self.project, self.group,
            self.development, self.writing, self.gate, self.validation,
        ])
        self.db.add_all([
            TaskDependency(task_id=32, predecessor_id=31),
            TaskDependency(task_id=33, predecessor_id=32),
            TaskDependency(task_id=34, predecessor_id=33),
        ])
        self.db.add(TimeSlot(
            id=1, schedule_run_id="run-1", task_id=31, instrument_id=10,
            plan_start=datetime(2026, 9, 2, 9, 0), plan_end=datetime(2026, 9, 2, 17, 0),
            status="scheduled", tier="confirmed",
        ))
        self.db.add(TimeSlot(
            id=2, schedule_run_id="run-1", task_id=32, instrument_id=None,
            plan_start=datetime(2026, 9, 3, 9, 0), plan_end=datetime(2026, 9, 3, 11, 30),
            status="scheduled", tier="confirmed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _breakdown(self, with_dependencies=True):
        tasks = load_diagnostic_resource_tasks(self.db, set(), current_project_id=1)
        tasks += load_bridge_candidate_tasks(self.db, {self.project.id})
        compat = build_compatibility(tasks, [self.instrument], True)
        dependencies = sorted(set(build_dependencies(
            tasks, load_task_children(self.db, tasks),
        ))) if with_dependencies else []
        _intervals, breakdown = _project_instrument_intervals(
            tasks, self.instrument.id, compat,
            datetime(2026, 9, 1), self.project.end_date, dependencies,
        )
        return breakdown

    def test_scheduled_bridging_task_counts_as_manual_occupancy(self):
        breakdown = self._breakdown()

        # 方案撰写已排（有时间槽）→ 人工占用；方法验证未排 → 预测工时
        self.assertEqual(8.0, breakdown["slot"])
        self.assertEqual(2.5, breakdown["bridge"])
        self.assertEqual(10.0, breakdown["forecast"])

    def test_unscheduled_bridging_task_counts_as_forecast(self):
        # 方案撰写还没排程时，它的工时属于预测而不是已排
        self.db.query(TimeSlot).filter(TimeSlot.task_id == self.writing.id).delete()
        self.writing.status = "pending"
        self.db.commit()

        breakdown = self._breakdown()

        self.assertEqual(0.0, breakdown["bridge"])
        self.assertEqual(12.5, breakdown["forecast"])

    def test_manual_task_without_bridge_does_not_occupy(self):
        # 去掉后续仪器任务，方案撰写就不再桥接任何仪器
        self.db.delete(self.validation)
        self.db.commit()

        breakdown = self._breakdown()

        self.assertEqual(8.0, breakdown["slot"])
        self.assertEqual(0.0, breakdown["bridge"])
        self.assertEqual(0.0, breakdown["forecast"])


if __name__ == "__main__":
    unittest.main()
