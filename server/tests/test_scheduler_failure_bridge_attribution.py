import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.scheduler_failure_diagnostics import _bridge_intervals


class SchedulerFailureBridgeAttributionTest(unittest.TestCase):
    """桥接任务的占用应始终计入人工占用，不因重排作废时间槽而改记预测工时。"""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.window_start = datetime(2026, 8, 28, 16, 0)
        self.window_end = datetime(2026, 9, 7, 23, 59, 59)
        project = Project(
            name="桥接归属", code="BRIDGE-ATTR", estimated_hours=10,
            start_date=self.window_start, end_date=self.window_end,
        )
        instrument = Instrument(
            code="BRIDGE-INST", name="桥接测试仪器",
            availability_status="available", status="idle",
        )
        self.db.add_all([project, instrument])
        self.db.flush()
        self.task = Task(
            project_id=project.id, name="方案撰写", task_type="QCFA_001",
            requires_instrument=False, requires_human=True, assignee_id=1,
            est_duration_hours=2.5, switchover_hours=0, status="scheduled",
        )
        self.db.add(self.task)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def _add_slot(self, status, lifecycle_status):
        self.db.add(TimeSlot(
            task_id=self.task.id, schedule_run_id="test", instrument_id=None,
            plan_start=datetime(2026, 9, 2, 14, 30), plan_end=datetime(2026, 9, 2, 17, 0),
            tier="confirmed", status=status, lifecycle_status=lifecycle_status,
            is_night_run=False,
        ))
        self.db.flush()
        self.db.refresh(self.task)

    def test_active_slot_reports_human_occupancy(self):
        self._add_slot("scheduled", "active")

        intervals = _bridge_intervals(self.task, self.window_start, self.window_end)

        self.assertEqual(["bridge"], [kind for *_rest, kind in intervals])
        self.assertEqual(datetime(2026, 9, 2, 14, 30), intervals[0][0])

    def test_superseded_slot_still_reports_human_occupancy(self):
        # 重排把原时间槽置为 superseded 后，占用性质不变，仍是人工占住仪器。
        # 若改记为预测工时，占用明细里的人工占用会凭空变成 0。
        self._add_slot("cancelled", "superseded")

        intervals = _bridge_intervals(self.task, self.window_start, self.window_end)

        self.assertEqual(["bridge"], [kind for *_rest, kind in intervals])
        occupied = sum(
            (end - start).total_seconds() / 3600 for start, end, _task, _kind in intervals
        )
        self.assertAlmostEqual(2.5, occupied, places=2)

    def test_repeated_supersede_does_not_double_count(self):
        # 反复重排会留下多份时间范围相同的作废时间槽，合并后仍只算一段占用。
        for _ in range(3):
            self._add_slot("cancelled", "superseded")

        intervals = _bridge_intervals(self.task, self.window_start, self.window_end)

        occupied = sum(
            (end - start).total_seconds() / 3600 for start, end, _task, _kind in intervals
        )
        self.assertAlmostEqual(2.5, occupied, places=2)

    def test_never_planned_task_stays_forecast(self):
        # 从未排过的桥接任务仍归预测工时，保持既有口径不变。
        intervals = _bridge_intervals(self.task, self.window_start, self.window_end)

        self.assertEqual(["forecast"], [kind for *_rest, kind in intervals])


if __name__ == "__main__":
    unittest.main()
