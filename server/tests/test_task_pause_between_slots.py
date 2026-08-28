"""任务在两个时间段之间的空档里也要能暂停。

任务被按天切成多个时间槽，上一段按计划边界结束、下一段还没到（隔夜、周末、
或被别的项目插队）时，没有"正在运行"的时间槽，但人确实还在这个任务上。
此前暂停会被拒绝，而同样状态下"完成"是允许的——两条路对同一个状态给出了
不同结论。
"""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.errors import DomainConflictError
from app.models import Instrument, Project, Task, TaskExecutionSegment, TimeSlot, User
from app.services.task_pause_service import _running_source


class PauseBetweenSlotsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        operator = User(username="tech", display_name="技术员", role="技术员")
        instrument = Instrument(code="LCMS-01", name="液质联用仪")
        project = Project(code="P", name="跨时段项目")
        self.db.add_all([operator, instrument, project])
        self.db.flush()
        self.task = Task(
            project_id=project.id, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, assignee_id=operator.id, status="running",
        )
        self.db.add(self.task)
        self.db.flush()
        # 上一段：已按计划边界结束；下一段：还在未来。
        self.done_slot = TimeSlot(
            task_id=self.task.id, instrument_id=instrument.id, schedule_run_id="r",
            plan_start=datetime(2026, 9, 4, 16, 0), plan_end=datetime(2026, 9, 4, 20, 0),
            tier="confirmed", status="completed", lifecycle_status="active",
            actual_start=datetime(2026, 9, 4, 16, 0), actual_end=datetime(2026, 9, 4, 20, 0),
        )
        self.next_slot = TimeSlot(
            task_id=self.task.id, instrument_id=instrument.id, schedule_run_id="r",
            plan_start=datetime(2026, 9, 7, 8, 30), plan_end=datetime(2026, 9, 7, 20, 0),
            tier="confirmed", status="scheduled", lifecycle_status="active",
        )
        self.db.add_all([self.done_slot, self.next_slot])
        self.db.flush()
        self.segment = TaskExecutionSegment(
            task_id=self.task.id, slot_id=self.done_slot.id,
            instrument_id=instrument.id, operator_id=operator.id,
            started_at=datetime(2026, 9, 4, 16, 0), ended_at=None,
        )
        self.db.add(self.segment)
        self.db.flush()
        self.db.refresh(self.task)

    def tearDown(self):
        self.db.close()

    def test_pause_is_allowed_between_slots(self):
        slot, task = _running_source(self.db, self.next_slot.id)

        self.assertEqual(self.done_slot.id, slot.id)
        self.assertEqual(self.task.id, task.id)

    def test_rejects_when_no_slot_was_ever_started(self):
        # 任务挂着 running 却没有任何时间槽真正开始过，是脏状态，仍要拒绝。
        self.done_slot.actual_start = None
        self.db.flush()

        with self.assertRaisesRegex(DomainConflictError, "任务与时间槽状态不一致"):
            _running_source(self.db, self.next_slot.id)

    def test_rejects_when_execution_segment_is_closed(self):
        self.segment.ended_at = datetime(2026, 9, 4, 20, 0)
        self.db.flush()
        self.db.refresh(self.task)

        with self.assertRaisesRegex(DomainConflictError, "任务与时间槽状态不一致"):
            _running_source(self.db, self.next_slot.id)

    def test_finished_slot_boundary_is_not_overwritten(self):
        """空档期暂停不该回头改写已按计划边界结束的时间槽。"""
        paused_at = datetime(2026, 9, 5, 10, 0)
        slot, _task = _running_source(self.db, self.next_slot.id)

        slot.actual_end = slot.actual_end or paused_at

        self.assertEqual(datetime(2026, 9, 4, 20, 0), slot.actual_end)


class TransitionWorkerBoundaryTest(unittest.TestCase):
    """计划结束早于实际开始时，不能写出"结束早于开始"的矛盾数据。"""

    def test_actual_end_is_never_before_actual_start(self):
        from app.services.task_slot_transition_worker import advance_running_tasks

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine, autoflush=False)()
        project = Project(code="P2", name="迟开工项目")
        instrument = Instrument(code="GC-01", name="气相色谱仪")
        db.add_all([project, instrument])
        db.flush()
        task = Task(project_id=project.id, name="检测", task_type="T",
                    requires_instrument=True, status="running")
        db.add(task)
        db.flush()
        late_start = datetime(2026, 9, 4, 23, 2)
        db.add_all([
            TimeSlot(task_id=task.id, instrument_id=instrument.id, schedule_run_id="r",
                     plan_start=datetime(2026, 9, 4, 19, 30), plan_end=datetime(2026, 9, 4, 20, 0),
                     tier="confirmed", status="running", lifecycle_status="active",
                     actual_start=late_start),
            TimeSlot(task_id=task.id, instrument_id=instrument.id, schedule_run_id="r",
                     plan_start=datetime(2026, 9, 7, 8, 30), plan_end=datetime(2026, 9, 7, 20, 0),
                     tier="confirmed", status="scheduled", lifecycle_status="active"),
        ])
        db.flush()
        db.refresh(task)

        advance_running_tasks(db, datetime(2026, 9, 5, 0, 0))

        slot = min(task.time_slots, key=lambda s: s.plan_start)
        self.assertEqual("completed", slot.status)
        self.assertGreaterEqual(slot.actual_end, slot.actual_start)
        db.close()


if __name__ == "__main__":
    unittest.main()
