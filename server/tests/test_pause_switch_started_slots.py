import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.task_pause_switch_context_service import build_pause_switch_context
from app.services.task_slot_transition_worker import advance_running_tasks


class PauseSwitchStartedSlotsTest(unittest.TestCase):
    """已发生的时间槽不能进入暂停切换的待作废清单。

    supersede_slot 会拒绝作废带有实际开始／结束时间的槽并抛异常，整个暂停切换
    随之变成 500。现实里的触发路径是：给今晚登记夜间运行 → 夜跑槽继承了源槽的
    running 状态 → 时间槽推进器给它填上未来的实际开始时间 → 再暂停就崩。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.now = datetime(2026, 8, 31, 12, 0)
        operator = User(id=1, username="op", display_name="操作员", role="技术员")
        instrument = Instrument(id=1, code="ZBYY-002-0001", name="液质联用仪", status="running")
        self.db.add_all([
            operator, instrument,
            Project(id=1, code="XM-A", name="项目A"),
            Project(id=2, code="XM-B", name="项目B"),
            Task(id=1, project_id=1, name="方法开发A", task_type="FFKF_001",
                 requires_instrument=True, assignee_id=1, status="running", est_duration_hours=8),
            Task(id=2, project_id=2, name="方法开发B", task_type="FFKF_001",
                 requires_instrument=True, assignee_id=1, status="scheduled", est_duration_hours=4),
        ])
        self.source_slot = TimeSlot(
            id=1, task_id=1, instrument_id=1, status="running", tier="confirmed",
            plan_start=self.now - timedelta(hours=3), plan_end=self.now + timedelta(hours=5),
            actual_start=self.now - timedelta(hours=3),
        )
        # 今晚的夜跑：开始时刻还没到，却已被写成 running 并带上实际开始时间
        self.night_slot = TimeSlot(
            id=2, task_id=1, instrument_id=1, status="running", tier="confirmed",
            is_night_run=True,
            plan_start=self.now + timedelta(hours=8), plan_end=self.now + timedelta(hours=16),
            actual_start=self.now + timedelta(hours=8),
        )
        self.target_slot = TimeSlot(
            id=3, task_id=2, instrument_id=1, status="scheduled", tier="confirmed",
            plan_start=self.now + timedelta(days=1), plan_end=self.now + timedelta(days=1, hours=4),
        )
        self.db.add_all([self.source_slot, self.night_slot, self.target_slot])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_started_slots_are_not_replaceable(self):
        context = build_pause_switch_context(self.db, self.source_slot, self.target_slot, self.now)

        started = [s for s in context.replaceable_slots if s.actual_start or s.actual_end]
        self.assertEqual([], started)

    def test_transition_worker_does_not_stamp_future_slots(self):
        self.night_slot.actual_start = None
        self.db.commit()

        advance_running_tasks(self.db, self.now)

        self.db.refresh(self.night_slot)
        self.assertIsNone(self.night_slot.actual_start)

    def test_transition_worker_stamps_slots_whose_start_has_arrived(self):
        self.night_slot.actual_start = None
        self.db.commit()

        advance_running_tasks(self.db, self.now + timedelta(hours=9))

        self.db.refresh(self.night_slot)
        self.assertEqual(self.night_slot.plan_start, self.night_slot.actual_start)


if __name__ == "__main__":
    unittest.main()
