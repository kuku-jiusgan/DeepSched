import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.errors import DomainConflictError
from app.models import Instrument, Project, Task, TaskDependency, TaskExecutionSegment, TimeSlot, User
from app.services.schedule_completion_service import complete_task_and_shift
from app.services.task_execution_service import start_task_execution
from app.services import task_execution_service as execution_service
from app.services import task_pause_service as pause_service
from app.services.task_pause_service import _approval_ready_time, list_switch_candidates, pause_and_switch_task


def _next_working_day() -> datetime:
    """明天起的第一个工作日（零点）。

    用例里的时间槽必须落在工作日的 8:30-20:00 之内。写死日期会随着时间推移
    变成过去，排程不会往回排；直接用"明天"则在周五、周六运行时落到休息日，
    排程被推到下周一，断言随之失败。
    """
    day = (datetime.now() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


class _FrozenDatetime(datetime):
    """把暂停服务里的"当前时刻"冻结到用例基准时刻。

    暂停用真实的 datetime.now() 计算已执行工时和切换时刻。用例把时间槽放在
    未来的工作日上以避开周末，若不冻结，"任务已经跑了一段"这个前提就不成立，
    已执行工时会被算成 0，被暂停任务的剩余时长断言随之失真。
    """

    frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen or datetime.now(tz)


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
        self.base_day = _next_working_day()
        now = self.base_day.replace(hour=10)
        self.source_task = Task(
            project_id=self.project_a.id,
            name="方法开发A",
            task_type="FFKF_001",
            requires_instrument=True,
            assignee_id=self.operator.id,
            status="scheduled",
        )
        self.target_task = Task(
            project_id=self.project_b.id,
            name="方法开发B",
            task_type="FFKF_001",
            requires_instrument=True,
            assignee_id=self.operator.id,
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

    def _freeze_now(self, moment: datetime) -> None:
        """把暂停服务里的"当前时刻"冻结到指定时刻。

        暂停用真实的 datetime.now() 计算已执行工时。用例把时间槽放在未来的
        工作日上以避开周末，因此凡是断言"已经跑了一段、只剩多少"的用例，都必须
        冻结当前时刻，否则已执行工时会被算成 0。不需要这个前提的用例不要冻结，
        它们依赖真实时间判断任务是否正在运行。
        """
        _FrozenDatetime.frozen = moment
        # 暂停会调用执行服务去启动接替任务，两边必须冻在同一时刻，否则接替任务
        # 的实际开始时间落在计划之外，会留下零长度的锚点时间槽。
        for module in (pause_service, execution_service):
            freezer = patch.object(module, "datetime", _FrozenDatetime)
            freezer.start()
            self.addCleanup(freezer.stop)

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

    def test_pause_ignores_superseded_running_slot(self):
        stale = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=self.source_slot.plan_start,
            plan_end=self.source_slot.plan_end,
            status="running",
            lifecycle_status="superseded",
            actual_start=datetime.now() - timedelta(minutes=5),
        )
        self.db.add(stale)
        self.db.commit()

        pause_and_switch_task(self.db, self.source_slot.id, "等待样品", self.operator)

        self.assertEqual("paused", self.source_task.status)
        self.assertEqual("paused", self.source_slot.status)

    def test_pause_and_switch_ignores_superseded_open_occupancy(self):
        stale = TimeSlot(
            task_id=self.source_task.id,
            instrument_id=self.instrument.id,
            plan_start=self.source_slot.plan_start,
            plan_end=self.source_slot.plan_end,
            status="running",
            lifecycle_status="superseded",
            actual_start=datetime.now() - timedelta(minutes=5),
        )
        self.db.add(stale)
        self.db.commit()

        pause_and_switch_task(
            self.db, self.source_slot.id, "紧急切换", self.operator, self.target_slot.id,
        )

        self.assertEqual("paused", self.source_task.status)
        self.assertEqual("running", self.target_task.status)

    def test_pause_records_progress_and_reorder_uses_remaining_ledger(self):
        self.source_task.est_duration_hours = 35
        self.source_task.executed_minutes = 120
        self.source_slot.plan_start = datetime.now() - timedelta(minutes=1)
        self.source_slot.plan_end = datetime.now() + timedelta(minutes=1)
        self.db.commit()

        pause_and_switch_task(
            self.db, self.source_slot.id, "等待样品", self.operator, self.target_slot.id,
        )
        self.db.commit()

        self.assertEqual(120, self.source_task.executed_minutes)
        source_minutes = self._total_minutes(self._future_slots(self.source_task.id))
        self.assertEqual(35 * 60 - 120, source_minutes)

    def test_pause_reorder_keeps_delay_added_workload(self):
        self.source_task.est_duration_hours = 1
        self.source_task.additional_planned_minutes = 44 * 60
        self.source_task.executed_minutes = 60
        self.source_slot.plan_start = datetime.now() - timedelta(minutes=1)
        self.source_slot.plan_end = datetime.now() + timedelta(minutes=1)
        self.db.commit()

        pause_and_switch_task(
            self.db, self.source_slot.id, "等待样品", self.operator, self.target_slot.id,
        )
        self.db.commit()

        source_minutes = self._total_minutes(self._future_slots(self.source_task.id))
        self.assertEqual(44 * 60, source_minutes)

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
        original_target_start = self.target_slot.plan_start
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
        self.assertLess(self.target_slot.plan_start, original_target_start)
        self.assertLessEqual(self.target_slot.plan_start, self.target_slot.actual_start)
        self.assertLess(
            self.target_slot.actual_start - self.target_slot.plan_start,
            timedelta(minutes=30),
        )
        self.assertEqual(2, self.db.query(TaskExecutionSegment).count())

    def test_pause_and_switch_moves_the_whole_target_before_source_remainder(self):
        # 断言依赖"源任务已跑一段、只剩剩余部分"，必须冻结当前时刻。
        self._freeze_now(self.base_day.replace(hour=10))
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
        self.assertEqual(240, self._total_minutes(source_slots))
        self.assertLessEqual(target_slots[-1].plan_end, source_slots[0].plan_start)
        self.assertEqual("running", target_slots[0].status)
        self.assertTrue(all(slot.status == "scheduled" for slot in target_slots[1:]))
        self.assertTrue(all(slot.status == "paused" for slot in source_slots))

    def test_pause_and_switch_resumes_source_before_target_followup(self):
        switch_time = self.base_day.replace(hour=10, minute=0)
        self.source_task.est_duration_hours = 4
        self.source_task.assignee_id = self.operator.id
        self.target_task.est_duration_hours = 3
        self.target_task.assignee_id = self.operator.id
        self.source_slot.plan_start = self.base_day.replace(hour=8, minute=30)
        self.source_slot.plan_end = self.base_day.replace(hour=11, minute=30)
        self.source_slot.actual_start = self.source_slot.plan_start
        self.target_slot.plan_start = self.source_slot.plan_end
        self.target_slot.plan_end = self.base_day.replace(hour=14, minute=30)
        followup_task = Task(
            project_id=self.project_b.id,
            name="方案撰写",
            task_type="QCFA_001",
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=1,
            plan_order=1,
        )
        self.db.add(followup_task)
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=followup_task.id,
            predecessor_id=self.target_task.id,
            dependency_type="continuous_successor",
        ))
        self.db.add(TimeSlot(
            task_id=followup_task.id,
            instrument_id=None,
            plan_start=self.target_slot.plan_end,
            plan_end=self.target_slot.plan_end + timedelta(hours=1),
            status="scheduled",
            tier="confirmed",
        ))
        self.db.commit()

        with patch("app.services.task_pause_service.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = switch_time
            pause_and_switch_task(
                self.db, self.source_slot.id, "优先处理目标项目", self.operator, self.target_slot.id,
            )
        self.db.commit()

        target_slots = self._task_slots(self.target_task.id)
        followup_slots = self._task_slots(followup_task.id)
        source_slots = self._future_slots(self.source_task.id)
        self.assertLessEqual(target_slots[-1].plan_end, source_slots[0].plan_start)
        self.assertLessEqual(target_slots[-1].plan_end, followup_slots[0].plan_start)
        self.assertLessEqual(followup_slots[-1].plan_end, source_slots[0].plan_start)

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
        # 断言依赖"源任务已跑一段、只剩剩余部分"，必须冻结当前时刻。
        self._freeze_now(self.base_day.replace(hour=10))
        now = _next_working_day().replace(hour=10)
        self.source_task.assignee_id = self.operator.id
        self.source_task.requires_human = True
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

    def test_pause_switch_replans_target_assignee_noninstrument_task_when_source_is_nonhuman(self):
        # 断言依赖"源任务已跑一段、只剩剩余部分"，必须冻结当前时刻。
        self._freeze_now(self.base_day.replace(hour=10))
        self.source_task.requires_human = False
        self.source_task.assignee_id = None
        self.source_task.est_duration_hours = 2
        self.target_task.requires_human = True
        self.target_task.assignee_id = self.operator.id
        self.target_task.est_duration_hours = 2
        switch_time = self.base_day.replace(hour=10, minute=0)
        self.source_slot.plan_start = self.base_day.replace(hour=8, minute=30)
        self.source_slot.plan_end = self.base_day.replace(hour=12, minute=0)
        self.source_slot.actual_start = self.source_slot.plan_start
        self.target_slot.plan_start = self.base_day.replace(hour=12, minute=0)
        self.target_slot.plan_end = self.base_day.replace(hour=14, minute=0)
        operator_task = Task(
            project_id=self.project_b.id,
            name="目标负责人非仪器任务",
            task_type="QCFA_001",
            requires_instrument=False,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=1,
        )
        queue_tail = Task(
            project_id=self.project_b.id,
            name="后续仪器任务",
            task_type="FFYZ_001",
            requires_instrument=True,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=1,
        )
        self.db.add_all([operator_task, queue_tail])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=operator_task.id, instrument_id=None,
                plan_start=self.base_day.replace(hour=14, minute=0),
                plan_end=self.base_day.replace(hour=15, minute=0), status="scheduled", tier="confirmed",
            ),
            TimeSlot(
                task_id=queue_tail.id, instrument_id=self.instrument.id,
                plan_start=self.base_day.replace(hour=15, minute=0),
                plan_end=self.base_day.replace(hour=16, minute=0), status="scheduled", tier="confirmed",
            ),
        ])
        self.db.commit()

        with patch("app.services.task_pause_service.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = switch_time
            pause_and_switch_task(
                self.db, self.source_slot.id, "切换任务", self.operator, self.target_slot.id,
            )
        self.db.commit()

        target_slots = self._task_slots(self.target_task.id)
        operator_slots = self._task_slots(operator_task.id)
        self.assertTrue(operator_slots)
        self.assertLessEqual(target_slots[-1].plan_end, operator_slots[0].plan_start)
        self.assertTrue(all(
            slot.status != "running" or slot.actual_start is not None
            for slot in self._task_slots(self.target_task.id)
        ))

    def test_pause_and_switch_reorders_future_task_after_target_queue(self):
        future_task = Task(
            project_id=self.project_a.id,
            name="后续任务",
            task_type="FFKF_001",
            requires_instrument=True,
            assignee_id=self.operator.id,
            status="scheduled",
            allow_split=False,
        )
        self.db.add(future_task)
        self.db.flush()
        future_slot = TimeSlot(
            task_id=future_task.id,
            instrument_id=self.instrument.id,
            plan_start=self.target_slot.plan_end + timedelta(days=5),
            plan_end=self.target_slot.plan_end + timedelta(days=5, hours=2),
            status="scheduled",
            tier="confirmed",
        )
        original_future_start = future_slot.plan_start
        self.db.add(future_slot)
        self.db.commit()

        pause_and_switch_task(
            self.db, self.source_slot.id, "切换任务", self.operator, self.target_slot.id,
        )

        future_slots = self._task_slots(future_task.id)
        self.assertLessEqual(self.target_slot.plan_end, future_slots[0].plan_start)
        self.assertLess(future_slots[0].plan_start, original_future_start)

    def test_pause_and_switch_rejects_task_that_exceeds_project_end_date(self):
        self.source_task.project.end_date = datetime.now() + timedelta(hours=1)
        self.db.commit()

        with self.assertRaisesRegex(DomainConflictError, "有效时间窗口不足"):
            pause_and_switch_task(
                self.db, self.source_slot.id, "切换任务", self.operator, self.target_slot.id,
            )

    def test_approval_gate_sets_downstream_earliest_start(self):
        expected_approval_at = datetime.now() + timedelta(days=7)
        gate = Task(
            project_id=self.project_a.id,
            name="方案签批",
            task_type="SP_GATE",
            is_external_gate=True,
            gate_status="waiting_approval",
            expected_approval_at=expected_approval_at,
            status="waiting_approval",
        )
        downstream = Task(
            project_id=self.project_a.id,
            name="方法验证",
            task_type="FFYZ_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.db.add_all([gate, downstream])
        self.db.flush()
        self.db.add(TaskDependency(task_id=downstream.id, predecessor_id=gate.id))
        self.db.commit()

        self.assertEqual(expected_approval_at, _approval_ready_time(self.db, downstream))

    def _future_slots(self, task_id: int) -> list[TimeSlot]:
        return (
            self.db.query(TimeSlot)
            .filter(
                TimeSlot.task_id == task_id,
                TimeSlot.actual_start.is_(None),
                TimeSlot.lifecycle_status == "active",
            )
            .order_by(TimeSlot.plan_start, TimeSlot.id)
            .all()
        )

    def _task_slots(self, task_id: int) -> list[TimeSlot]:
        return (
            self.db.query(TimeSlot)
            .filter(TimeSlot.task_id == task_id, TimeSlot.lifecycle_status == "active")
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
        self.assertEqual(resumed_slots[0].actual_start, resumed_slots[0].plan_start)
        self.assertIsNotNone(self.source_slot.actual_end)
        self.assertEqual("completed", self.target_task.status)

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
        self.assertEqual("scheduled", later_slot.status)
        self.assertIsNotNone(earlier_slot.actual_start)
        self.assertIsNone(later_slot.actual_start)


if __name__ == "__main__":
    unittest.main()
