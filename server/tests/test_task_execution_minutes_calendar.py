"""已执行工时必须按工作日历统计，切换准备时间必须留在求解器时长里。

executed_minutes 会被 planned_task_minutes 减去，决定重排时还要排多久，
也决定任务能否标记完成，两者都是工作量口径。
"""

import unittest
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskExecutionSegment, TimeSlot
from app.services.scheduler_task_duration import remaining_duration_units
from app.services.task_pause_service import _elapsed_execution_minutes


class ElapsedExecutionMinutesTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        project = Project(
            name="工时口径", code="ELAPSED-CAL",
            start_date=datetime(2026, 8, 28), end_date=datetime(2026, 9, 30, 23, 59, 59),
        )
        self.instrument = Instrument(
            code="ELAPSED-INST", name="工时测试仪器",
            availability_status="available", status="idle",
        )
        self.db.add_all([project, self.instrument])
        self.db.flush()
        self.task = Task(
            project_id=project.id, name="方法开发", task_type="QCFA_001",
            requires_instrument=True, requires_human=True, assignee_id=1,
            est_duration_hours=8, switchover_hours=0, status="running",
        )
        self.db.add(self.task)
        self.db.flush()
        slot = TimeSlot(
            task_id=self.task.id, schedule_run_id="test",
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 28, 18, 0), plan_end=datetime(2026, 8, 31, 10, 0),
            tier="confirmed", status="running", lifecycle_status="active",
            is_night_run=False, actual_start=datetime(2026, 8, 28, 18, 0),
        )
        self.db.add(slot)
        self.db.flush()
        self.db.add(TaskExecutionSegment(
            task_id=self.task.id, slot_id=slot.id, instrument_id=self.instrument.id,
            started_at=datetime(2026, 8, 28, 18, 0), ended_at=None,
        ))
        self.db.flush()
        self.db.refresh(self.task)

    def tearDown(self):
        self.db.close()

    def test_nights_and_weekends_are_excluded(self):
        # 周五 18:00 到周一 10:00，墙钟 64 小时（3840 分钟）。
        minutes = _elapsed_execution_minutes(
            self.db, self.task, datetime(2026, 8, 31, 10, 0),
        )

        self.assertLess(minutes, 8 * 60)
        self.assertGreater(minutes, 0)

    def test_same_working_day_span_is_counted_in_full(self):
        minutes = _elapsed_execution_minutes(
            self.db, self.task, datetime(2026, 8, 28, 19, 0),
        )

        self.assertEqual(60, minutes)


class RemainingDurationSwitchoverTest(unittest.TestCase):
    """切换准备时间不能在剩余时长推算里被丢掉。"""

    def _remaining_units(self, task, duration_units):
        return remaining_duration_units(
            task, duration_units, [], [0], {}, datetime(2026, 8, 28), 100,
        )

    def test_switchover_survives_the_executed_minutes_branch(self):
        task = SimpleNamespace(
            id=1, est_duration_hours=4, switchover_hours=0.5,
            executed_minutes=60, additional_planned_minutes=0,
            execution_segments=[],
        )

        # 剩余工作量 3h = 6 单元，加上 0.5h 切换 1 单元。
        self.assertEqual(7, self._remaining_units(task, 9))

    def test_approved_delay_hours_still_counted(self):
        task = SimpleNamespace(
            id=1, est_duration_hours=4, switchover_hours=0,
            executed_minutes=0, additional_planned_minutes=120,
            execution_segments=[],
        )

        self.assertEqual(12, self._remaining_units(task, 8))


if __name__ == "__main__":
    unittest.main()
