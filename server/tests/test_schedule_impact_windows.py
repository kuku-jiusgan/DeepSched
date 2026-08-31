import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.schedule_advance_notification_service import capture_task_schedule_windows
from app.services.schedule_insert_resources import anchor_schedule_end


class ScheduleImpactWindowTest(unittest.TestCase):
    """排程影响弹窗里的「原计划」只能看现行时间槽。

    把历次被推翻的作废槽一起取最早开始、最晚结束，得到的是所有版本的并集：
    一个 2.5 小时的任务会显示成横跨数天，看上去像被莫名拉长，而它其实压根没动。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add_all([
            Project(id=1, code="XM-1", name="项目一"),
            Instrument(id=1, code="INST", name="仪器"),
            Task(id=1, project_id=1, name="方案撰写", task_type="QCFA_001",
                 requires_instrument=False, est_duration_hours=2.5, status="scheduled"),
        ])
        base = datetime(2026, 9, 3, 14, 0)
        self.db.add_all([
            # 现行计划：2.5 小时
            TimeSlot(id=1, task_id=1, plan_start=base, plan_end=base + timedelta(hours=2.5),
                     status="scheduled", lifecycle_status="active", schedule_run_id="r2"),
            # 被推翻的旧版本，排在五天后
            TimeSlot(id=2, task_id=1, plan_start=base + timedelta(days=5),
                     plan_end=base + timedelta(days=5, hours=2.5),
                     status="cancelled", lifecycle_status="superseded", schedule_run_id="r1"),
        ])
        self.db.commit()
        self.base = base

    def tearDown(self):
        self.db.close()

    def test_window_covers_only_the_current_plan(self):
        window = capture_task_schedule_windows(self.db, {1})[1]

        self.assertEqual(self.base, window[0])
        self.assertEqual(self.base + timedelta(hours=2.5), window[1])

    def test_window_span_matches_the_planned_hours(self):
        start, end = capture_task_schedule_windows(self.db, {1})[1]

        self.assertEqual(2.5, (end - start).total_seconds() / 3600)

    def test_insert_anchor_ignores_superseded_slots(self):
        self.assertEqual(self.base + timedelta(hours=2.5), anchor_schedule_end(self.db, 1))


if __name__ == "__main__":
    unittest.main()
