import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.errors import DomainConflictError
from app.models import Instrument, Project, Task, TaskDependency, TaskExecutionSegment, TimeSlot, User
from app.services.schedule_completion_service import complete_task_and_shift
from app.services.task_execution_service import start_task_execution
from app.services.task_pause_service import list_switch_candidates, pause_and_switch_task


class TaskPauseServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        self.operator = User(username="tech", display_name="技术员", role="技术员")
        self.instrument = Instrument(code="LCMS-01", name="液质联用仪")
        self.project_a = Project(code="A", name="项目A")
        self.project_b = Project(code="B", name="项目B")
        self.db.add_all([self.operator, self.instrument, self.project_a, self.project_b])
        self.db.flush()
        now = datetime.now()
        self.source_task = Task(
            project_id=self.project_a.id,
            name="方法开发A",
            task_type="FFKF_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.target_task = Task(
            project_id=self.project_b.id,
            name="方法开发B",
            task_type="FFKF_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.db.add_all([self.source_task, self.target_task])
        self.db.flush()
        self.source_slot = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=now - timedelta(hours=1),
            plan_end=now + timedelta(hours=2),
            status="scheduled",
            tier="confirmed",
        )
        self.target_slot = TimeSlot(
            task_id=self.target_task.id,
            instrument_id=self.instrument.id,
            plan_start=now + timedelta(hours=2),
            plan_end=now + timedelta(hours=5),
            status="scheduled",
            tier="confirmed",
        )
        self.db.add_all([self.source_slot, self.target_slot])
        self.db.commit()
        start_task_execution(self.db, self.source_slot.id, self.operator.id)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_pause_releases_instrument_without_completing_task(self):
        result = pause_and_switch_task(
            self.db, self.source_slot.id, "等待样品", self.operator,
        )
        self.db.commit()

        self.assertEqual("ok", result["status"])
        self.assertEqual("paused", self.source_task.status)
        self.assertEqual("paused", self.source_slot.status)
        self.assertIsNotNone(self.source_slot.actual_end)
        segment = self.db.query(TaskExecutionSegment).one()
        self.assertEqual("paused", segment.end_reason)
        self.assertEqual("等待样品", segment.pause_reason)

    def test_pause_marks_continuous_running_slots_as_paused(self):
        followup_slot = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=self.source_slot.plan_end,
            plan_end=self.source_slot.plan_end + timedelta(hours=3),
            status="running",
            tier="confirmed",
        )
        self.db.add(followup_slot)
        self.db.commit()

        pause_and_switch_task(self.db, self.source_slot.id, "等待样品", self.operator)
        self.db.commit()

        self.assertEqual("paused", self.source_slot.status)
        self.assertEqual("paused", followup_slot.status)
        self.assertIsNotNone(self.source_slot.actual_end)
        self.assertIsNone(followup_slot.actual_end)

    def test_pause_marks_future_running_slots_on_other_instruments_as_paused(self):
        other_instrument = Instrument(code="LCMS-02", name="液质联用仪2")
        self.db.add(other_instrument)
        self.db.flush()
        followup_slot = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=other_instrument.id,
            plan_start=self.source_slot.plan_end,
            plan_end=self.source_slot.plan_end + timedelta(hours=3),
            status="running",
            tier="confirmed",
        )
        self.db.add(followup_slot)
        self.db.commit()

        pause_and_switch_task(self.db, self.source_slot.id, "等待样品", self.operator)
        self.db.commit()

        self.assertEqual("paused", self.source_task.status)
        self.assertEqual("paused", self.source_slot.status)
        self.assertEqual("paused", followup_slot.status)

    def test_pause_and_switch_starts_selected_candidate(self):
        pause_and_switch_task(
            self.db,
            self.source_slot.id,
            "紧急插单",
            self.operator,
            self.target_slot.id,
        )
        self.db.commit()

        self.assertEqual("paused", self.source_task.status)
        self.assertEqual("running", self.target_task.status)
        self.assertIsNotNone(self.target_slot.actual_start)
        self.assertIsNone(self.target_slot.actual_end)
        self.assertEqual(2, self.db.query(TaskExecutionSegment).count())

    def test_pause_and_switch_moves_the_whole_target_before_source_remainder(self):
        target_followup = TimeSlot(
            task_id=self.target_task.id,
            instrument_id=self.instrument.id,
            plan_start=self.target_slot.plan_end,
            plan_end=self.target_slot.plan_end + timedelta(hours=2),
            status="scheduled",
            tier="confirmed",
        )
        source_followup = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=self.target_slot.plan_end + timedelta(hours=2),
            plan_end=self.target_slot.plan_end + timedelta(hours=4),
            status="running",
            tier="confirmed",
        )
        self.db.add_all([target_followup, source_followup])
        self.db.commit()

        pause_and_switch_task(
            self.db,
            self.source_slot.id,
            "切换任务",
            self.operator,
            self.target_slot.id,
        )
        self.db.commit()

        target_slots = self._task_slots(self.target_task.id)
        source_slots = self._future_slots(self.source_task.id)
        self.assertEqual(300, self._total_minutes(target_slots))
        self.assertEqual(300, self._total_minutes(source_slots))
        self.assertLessEqual(target_slots[-1].plan_end, source_slots[0].plan_start)
        self.assertTrue(all(slot.status == "running" for slot in target_slots))
        self.assertTrue(all(slot.status == "paused" for slot in source_slots))

    def test_pause_and_switch_does_not_create_weekend_ranges(self):
        friday = datetime(2026, 8, 7, 19, 30)
        self.source_slot.plan_start = friday - timedelta(hours=1)
        self.source_slot.plan_end = friday + timedelta(hours=2)
        self.source_slot.actual_start = friday - timedelta(minutes=30)
        self.target_slot.plan_start = datetime(2026, 8, 10, 8, 30)
        self.target_slot.plan_end = datetime(2026, 8, 10, 12, 30)
        self.db.commit()

        from app.services.task_pause_service import _insert_target_into_source_schedule

        _insert_target_into_source_schedule(
            self.db,
            self.source_slot,
            self.target_slot,
            friday,
        )
        self.db.flush()

        future_slots = self._future_slots(self.target_task.id) + self._future_slots(self.source_task.id)
        self.assertTrue(future_slots)
        self.assertTrue(all(slot.plan_start.weekday() < 5 for slot in future_slots))
        self.assertTrue(all(slot.plan_end.weekday() < 5 for slot in future_slots))

    def test_pause_and_switch_shifts_all_slots_of_intermediate_tasks(self):
        now = datetime.now()
        self.source_task.assignee_id = self.operator.id
        self.target_slot.plan_start = now + timedelta(hours=5)
        self.target_slot.plan_end = now + timedelta(hours=8)
        intermediate_task = Task(
            project_id=self.project_a.id,
            name="中间任务",
            task_type="FFKF_001",
            requires_human=True,
            requires_instrument=False,
            assignee_id=self.operator.id,
            status="scheduled",
        )
        self.db.add(intermediate_task)
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=intermediate_task.id,
                instrument_id=None,
                plan_start=now + timedelta(hours=3),
                plan_end=now + timedelta(hours=4),
                status="scheduled",
                tier="confirmed",
            ),
            TimeSlot(
                task_id=intermediate_task.id,
                instrument_id=None,
                plan_start=now + timedelta(hours=8),
                plan_end=now + timedelta(hours=9),
                status="scheduled",
                tier="confirmed",
            ),
        ])
        self.db.commit()

        pause_and_switch_task(
            self.db,
            self.source_slot.id,
            "切换任务",
            self.operator,
            self.target_slot.id,
        )
        self.db.commit()

        target_slots = self._task_slots(self.target_task.id)
        source_slots = self._future_slots(self.source_task.id)
        intermediate_slots = self._task_slots(intermediate_task.id)
        self.assertEqual(120, self._total_minutes(intermediate_slots))
        self.assertLessEqual(target_slots[-1].plan_end, source_slots[0].plan_start)
        self.assertLessEqual(source_slots[-1].plan_end, intermediate_slots[0].plan_start)

    def _future_slots(self, task_id: int) -> list[TimeSlot]:
        return (
            self.db.query(TimeSlot)
            .filter(TimeSlot.task_id == task_id, TimeSlot.actual_start.is_(None))
            .order_by(TimeSlot.plan_start, TimeSlot.id)
            .all()
        )

    def _task_slots(self, task_id: int) -> list[TimeSlot]:
        return (
            self.db.query(TimeSlot)
            .filter(TimeSlot.task_id == task_id)
            .order_by(TimeSlot.plan_start, TimeSlot.id)
            .all()
        )

    @staticmethod
    def _total_minutes(slots: list[TimeSlot]) -> int:
        return sum(int((slot.plan_end - slot.plan_start).total_seconds() / 60) for slot in slots)

    def test_completing_replacement_resumes_paused_source_task(self):
        pause_and_switch_task(
            self.db,
            self.source_slot.id,
            "紧急插单",
            self.operator,
            self.target_slot.id,
        )
        self.db.commit()

        result = complete_task_and_shift(
            self.db,
            self.target_task.id,
            actual_end_time=datetime.now(),
            completed_slot_id=self.target_slot.id,
            release_instrument=True,
        )
        self.db.commit()

        self.assertEqual("ok", result["status"])
        self.assertEqual(self.source_task.id, result["resumed_task_id"])
        self.assertEqual("running", self.source_task.status)
        self.assertIsNotNone(self.source_slot.actual_end)
        resumed_slots = [
            slot for slot in self.source_task.time_slots
            if slot.status == "running" and slot.actual_start is not None
        ]
        self.assertEqual(1, len(resumed_slots))
        self.assertIsNotNone(self.source_slot.actual_end)
        self.assertEqual("done", self.target_task.status)

    def test_candidate_with_paused_predecessor_is_excluded(self):
        dependent_task = Task(
            project_id=self.project_a.id,
            name="方案撰写",
            task_type="QCFA_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.db.add(dependent_task)
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=dependent_task.id,
            predecessor_id=self.source_task.id,
        ))
        self.db.add(TimeSlot(
            task_id=dependent_task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime.now() + timedelta(hours=5),
            plan_end=datetime.now() + timedelta(hours=6),
            status="scheduled",
            tier="confirmed",
        ))
        self.db.commit()

        candidates = list_switch_candidates(self.db, self.source_slot.id)

        self.assertEqual([self.target_task.id], [item["task_id"] for item in candidates])

    def test_switch_candidates_rejects_inconsistent_running_state(self):
        self.source_slot.status = "scheduled"
        self.source_slot.actual_start = None
        self.db.commit()

        with self.assertRaisesRegex(DomainConflictError, "任务与时间槽状态不一致"):
            list_switch_candidates(self.db, self.source_slot.id)

    def test_paused_task_can_resume_with_new_execution_segment(self):
        pause_and_switch_task(self.db, self.source_slot.id, "等待样品", self.operator)
        self.db.commit()

        start_task_execution(self.db, self.source_slot.id, self.operator.id)
        self.db.commit()

        self.assertEqual("running", self.source_task.status)
        self.assertIsNone(self.source_slot.actual_end)
        self.assertEqual(2, self.db.query(TaskExecutionSegment).count())

    def test_resume_from_later_slot_clears_future_paused_residue(self):
        now = datetime.now()
        pause_and_switch_task(self.db, self.source_slot.id, "等待样品", self.operator)
        self.source_slot.plan_end = now - timedelta(minutes=1)
        earlier_slot = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=now,
            plan_end=now + timedelta(hours=2),
            status="paused",
            tier="confirmed",
        )
        later_slot = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=now + timedelta(hours=2),
            plan_end=now + timedelta(hours=4),
            status="paused",
            tier="confirmed",
        )
        self.db.add_all([earlier_slot, later_slot])
        self.db.commit()

        start_task_execution(self.db, later_slot.id, self.operator.id)
        self.db.commit()

        self.assertEqual("running", self.source_task.status)
        self.assertEqual("running", earlier_slot.status)
        self.assertEqual("running", later_slot.status)
        self.assertIsNotNone(earlier_slot.actual_start)
        self.assertIsNone(later_slot.actual_start)


if __name__ == "__main__":
    unittest.main()
