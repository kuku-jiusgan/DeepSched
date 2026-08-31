import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskNightRun, TimeSlot
from app.services.instrument_utilization_service import _night_ranges
from app.services.schedule_slot_change_log_service import supersede_slot


class NightRunInvalidationTest(unittest.TestCase):
    """夜跑时间槽作废时，夜间运行登记要同步作废而不是留着。

    夜跑有两份数据：时间槽上的 is_night_run 标记，和独立的夜跑登记表。只作废
    时间槽的话，项目工时统计报表（按时间槽算）里这几小时消失了，仪器利用率
    报表（读登记表）却仍把它算成一次真实发生的夜跑。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.start = datetime(2026, 8, 31, 20, 0)
        self.end = self.start + timedelta(hours=8)
        self.db.add_all([
            Project(id=1, code="XM-A", name="项目A"),
            Instrument(id=1, code="ZBYY-002-0001", name="液质联用仪"),
            Task(id=1, project_id=1, name="方法开发", task_type="FFKF_001",
                 requires_instrument=True, status="running"),
        ])
        self.slot = TimeSlot(
            id=1, task_id=1, instrument_id=1, is_night_run=True,
            plan_start=self.start, plan_end=self.end,
            status="scheduled", tier="confirmed", schedule_run_id="run-1",
        )
        self.db.add(self.slot)
        self.db.flush()
        self.record = TaskNightRun(
            task_id=1, slot_id=self.slot.id, instrument_id=1,
            started_at=self.start, ended_at=self.end,
        )
        self.db.add(self.record)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_superseding_a_night_slot_marks_the_record(self):
        supersede_slot(self.db, self.slot, "暂停切换重排")
        self.db.commit()

        self.assertEqual("superseded", self.record.lifecycle_status)
        self.assertEqual("暂停切换重排", self.record.superseded_reason)
        self.assertIsNotNone(self.record.superseded_at)

    def test_record_is_kept_not_deleted(self):
        supersede_slot(self.db, self.slot, "暂停切换重排")
        self.db.commit()

        self.assertEqual(1, self.db.query(TaskNightRun).count())

    def test_utilization_stops_counting_a_cancelled_night_run(self):
        window = (self.start - timedelta(hours=1), self.end + timedelta(hours=1))
        self.assertEqual(1, len(_night_ranges(self.db, 1, *window)))

        supersede_slot(self.db, self.slot, "暂停切换重排")
        self.db.commit()

        self.assertEqual([], _night_ranges(self.db, 1, *window))

    def test_ordinary_slot_does_not_touch_night_run_records(self):
        plain = TimeSlot(
            id=2, task_id=1, instrument_id=1, is_night_run=False,
            plan_start=self.start - timedelta(hours=6), plan_end=self.start,
            status="scheduled", tier="confirmed", schedule_run_id="run-1",
        )
        self.db.add(plain)
        self.db.commit()

        supersede_slot(self.db, plain, "排程重排")
        self.db.commit()

        self.assertEqual("active", self.record.lifecycle_status)


if __name__ == "__main__":
    unittest.main()
