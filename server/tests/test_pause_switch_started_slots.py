import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskNightRun, TimeSlot, User
from app.services.instrument_utilization_service import _night_ranges
from app.services.task_pause_service import list_switch_candidates
from app.services.task_pause_solver_service import apply_switch_anchors
from app.services.task_pause_switch_context_service import build_pause_switch_context


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

    def _add_night_run(self, task_id: int, end: datetime) -> tuple[TimeSlot, TaskNightRun]:
        """夜间运行直接使用登记区间，actual_start/actual_end 正常留空。"""
        slot = TimeSlot(
            task_id=task_id, instrument_id=1, status="running", tier="confirmed",
            is_night_run=True, plan_start=end - timedelta(hours=8), plan_end=end,
        )
        self.db.add(slot)
        self.db.flush()
        record = TaskNightRun(
            task_id=task_id, slot_id=slot.id, instrument_id=1,
            started_at=slot.plan_start, ended_at=end,
        )
        self.db.add(record)
        self.db.commit()
        return slot, record

    def test_past_night_runs_are_not_listed_for_cancellation(self):
        self._add_night_run(1, self.now - timedelta(days=9))
        self._add_night_run(2, self.now - timedelta(days=8))
        future_slot, _ = self._add_night_run(1, self.now + timedelta(hours=16))

        with patch("app.services.task_pause_service.datetime") as clock:
            clock.now.return_value = self.now
            candidates = list_switch_candidates(self.db, self.source_slot.id)

        self.assertEqual(1, len(candidates))
        self.assertEqual(
            [(future_slot.plan_start, future_slot.plan_end)],
            [(run["plan_start"], run["plan_end"])
             for run in candidates[0]["cancelled_night_runs"]],
        )

    def test_switch_preserves_past_night_records_but_cancels_future_records(self):
        past_source, source_record = self._add_night_run(1, self.now - timedelta(days=9))
        past_target, target_record = self._add_night_run(2, self.now - timedelta(days=8))
        future_slot, future_record = self._add_night_run(1, self.now + timedelta(hours=16))
        context = build_pause_switch_context(self.db, self.source_slot, self.target_slot, self.now)

        apply_switch_anchors(self.db, context, self.source_slot, self.target_slot)
        self.db.commit()

        for slot, record in ((past_source, source_record), (past_target, target_record)):
            self.assertEqual("active", slot.lifecycle_status)
            self.assertEqual("running", slot.status)
            self.assertEqual("active", record.lifecycle_status)
            self.assertIsNone(record.superseded_at)
            self.assertIsNone(slot.actual_start)
            self.assertIsNone(slot.actual_end)
            self.assertEqual(
                [(slot.plan_start, slot.plan_end)],
                _night_ranges(self.db, 1, slot.plan_start, slot.plan_end),
            )
        self.assertEqual("superseded", future_slot.lifecycle_status)
        self.assertEqual("superseded", future_record.lifecycle_status)

    def test_night_run_ending_at_switch_time_is_not_cancelled(self):
        ended_slot, _ = self._add_night_run(1, self.now)
        overlapping_slot, _ = self._add_night_run(1, self.now + timedelta(hours=1))
        context = build_pause_switch_context(self.db, self.source_slot, self.target_slot, self.now)

        self.assertNotIn(ended_slot.id, {slot.id for slot in context.replaceable_slots})
        self.assertIn(overlapping_slot.id, {slot.id for slot in context.replaceable_slots})

if __name__ == "__main__":
    unittest.main()
