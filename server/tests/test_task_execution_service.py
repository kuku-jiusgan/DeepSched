import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    Instrument,
    Project,
    Task,
    TaskDependency,
    TaskExecutionSegment,
    TimeSlot,
)
from app.services.task_execution_service import (
    TaskExecutionInvalidError,
    start_task_execution,
)


class TaskExecutionServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.project = Project(id=1, name="项目", code="P-001")
        self.predecessor = Task(
            id=1,
            project_id=1,
            name="方法开发",
            task_type="FFKF_001",
            status="running",
        )
        self.task = Task(
            id=2,
            project_id=1,
            name="方案撰写",
            task_type="QCFA_001",
            requires_instrument=True,
            status="scheduled",
        )
        self.slot = TimeSlot(
            id=1,
            task_id=2,
            instrument_id=1,
            plan_start=datetime.now() - timedelta(minutes=30),
            plan_end=datetime.now() + timedelta(minutes=30),
            status="scheduled",
            tier="confirmed",
        )
        self.db.add_all([self.project, self.predecessor, self.task, self.slot])
        self.db.add(TaskDependency(task_id=2, predecessor_id=1))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_incomplete_predecessor_blocks_start(self):
        with self.assertRaisesRegex(TaskExecutionInvalidError, "方法开发.*尚未完成"):
            start_task_execution(self.db, self.slot.id)

        self.assertEqual("scheduled", self.db.get(Task, self.task.id).status)

    def test_completed_predecessor_allows_start(self):
        self.predecessor.status = "done"
        self.db.commit()

        result = start_task_execution(self.db, self.slot.id)

        self.assertEqual("ok", result["status"])
        self.assertEqual("running", self.db.get(Task, self.task.id).status)
        self.assertEqual("running", self.db.get(TimeSlot, self.slot.id).status)
        self.assertIsNotNone(self.db.get(TimeSlot, self.slot.id).actual_start)

    def test_parent_predecessor_uses_all_leaf_children(self):
        self.predecessor.task_type = "group"
        self.predecessor.status = "pending"
        lcms = Task(
            id=3,
            project_id=1,
            parent_id=self.predecessor.id,
            name="LCMS方法开发",
            task_type="FFKF_001",
            status="done",
        )
        gcms = Task(
            id=4,
            project_id=1,
            parent_id=self.predecessor.id,
            name="GCMS方法开发",
            task_type="FFKF_001",
            status="running",
        )
        self.db.add_all([lcms, gcms])
        self.db.commit()

        with self.assertRaisesRegex(TaskExecutionInvalidError, "GCMS方法开发.*尚未完成"):
            start_task_execution(self.db, self.slot.id)

        gcms.status = "done"
        self.db.commit()
        result = start_task_execution(self.db, self.slot.id)

        self.assertEqual("ok", result["status"])
        self.assertEqual("running", self.db.get(Task, self.task.id).status)

    def test_instrument_task_can_start_early_when_instrument_is_idle(self):
        self.predecessor.status = "done"
        self.slot.plan_start = datetime.now() + timedelta(hours=1)
        self.slot.plan_end = datetime.now() + timedelta(hours=2)
        self.db.commit()

        result = start_task_execution(self.db, self.slot.id)

        self.assertEqual("ok", result["status"])
        self.assertEqual("running", self.db.get(Task, self.task.id).status)

    def test_faulted_instrument_cannot_start_task(self):
        self.predecessor.status = "done"
        self.db.add(Instrument(
            id=1,
            code="ZBYY-002-0005",
            name="三重四极液质联用仪",
            status="fault",
        ))
        self.db.commit()

        with self.assertRaisesRegex(
            TaskExecutionInvalidError,
            "ZBYY-002-0005.*故障状态",
        ):
            start_task_execution(self.db, self.slot.id)

        self.assertEqual("scheduled", self.db.get(Task, self.task.id).status)

    def test_earlier_instrument_task_error_identifies_project(self):
        self.predecessor.status = "done"
        earlier_project = Project(id=2, name="前序项目", code="XM-002")
        earlier_task = Task(
            id=3,
            project_id=earlier_project.id,
            name="方法开发",
            task_type="FFKF_001",
            requires_instrument=True,
            status="scheduled",
        )
        earlier_slot = TimeSlot(
            id=2,
            task_id=earlier_task.id,
            instrument_id=1,
            plan_start=self.slot.plan_start - timedelta(hours=2),
            plan_end=self.slot.plan_start + timedelta(minutes=15),
            status="scheduled",
            tier="confirmed",
        )
        self.db.add_all([earlier_project, earlier_task, earlier_slot])
        self.db.commit()

        with self.assertRaisesRegex(
            TaskExecutionInvalidError,
            "仪器前序项目【XM-002 · 前序项目】任务【方法开发】尚未完成",
        ):
            start_task_execution(self.db, self.slot.id)

    def test_human_task_can_start_early(self):
        self.predecessor.status = "done"
        self.task.requires_instrument = False
        self.slot.instrument_id = None
        self.slot.plan_start = datetime.now() + timedelta(hours=1)
        self.slot.plan_end = datetime.now() + timedelta(hours=2)
        self.db.commit()

        result = start_task_execution(self.db, self.slot.id)

        self.assertEqual("ok", result["status"])
        self.assertEqual("running", self.db.get(Task, self.task.id).status)

    def test_instrument_task_cannot_start_early_when_instrument_is_running(self):
        self.predecessor.status = "done"
        self.slot.plan_start = datetime.now() + timedelta(hours=1)
        self.slot.plan_end = datetime.now() + timedelta(hours=2)
        occupying_task = Task(
            id=3,
            project_id=1,
            name="当前方法验证",
            task_type="FFYZ_001",
            requires_instrument=True,
            status="running",
        )
        occupying_slot = TimeSlot(
            id=2,
            task_id=3,
            instrument_id=1,
            plan_start=datetime.now() - timedelta(hours=1),
            plan_end=datetime.now() + timedelta(minutes=30),
            actual_start=datetime.now() - timedelta(hours=1),
            status="running",
            tier="confirmed",
        )
        self.db.add_all([occupying_task, occupying_slot])
        self.db.commit()

        with self.assertRaisesRegex(TaskExecutionInvalidError, "当前方法验证.*不能启动"):
            start_task_execution(self.db, self.slot.id)

    def test_overdue_instrument_task_cannot_start_while_instrument_is_occupied(self):
        self.predecessor.status = "done"
        occupying_task = Task(
            id=3,
            project_id=1,
            name="延期中的方法开发",
            task_type="FFKF_001",
            requires_instrument=True,
            status="running",
        )
        occupying_slot = TimeSlot(
            id=2,
            task_id=3,
            instrument_id=1,
            plan_start=datetime.now() - timedelta(hours=2),
            plan_end=datetime.now() - timedelta(hours=1),
            actual_start=datetime.now() - timedelta(hours=2),
            status="running",
            tier="confirmed",
        )
        self.db.add_all([occupying_task, occupying_slot])
        self.db.commit()

        with self.assertRaisesRegex(TaskExecutionInvalidError, "延期中的方法开发.*不能启动"):
            start_task_execution(self.db, self.slot.id)

    def test_cannot_start_while_another_task_is_between_its_slots(self):
        """仪器上的任务跨时间段还没做完时，别的任务不能在空档里开起来。

        任务被按天切成多段，上一段按计划边界结束、下一段还没到，中间没有任何
        在跑的时间槽，但执行流水没结束，仪器仍被它占着。
        """
        self.predecessor.status = "done"
        occupying_task = Task(
            id=3,
            project_id=1,
            name="跨天的方法开发",
            task_type="FFKF_001",
            requires_instrument=True,
            status="running",
        )
        occupying_slot = TimeSlot(
            id=2,
            task_id=3,
            instrument_id=1,
            plan_start=datetime.now() - timedelta(days=1, hours=2),
            plan_end=datetime.now() - timedelta(days=1),
            actual_start=datetime.now() - timedelta(days=1, hours=2),
            actual_end=datetime.now() - timedelta(days=1),
            status="completed",
            tier="confirmed",
        )
        self.db.add_all([occupying_task, occupying_slot])
        self.db.flush()
        self.db.add(TaskExecutionSegment(
            task_id=3,
            slot_id=2,
            instrument_id=1,
            started_at=datetime.now() - timedelta(days=1, hours=2),
        ))
        self.db.commit()

        with self.assertRaisesRegex(TaskExecutionInvalidError, "跨天的方法开发.*不能启动"):
            start_task_execution(self.db, self.slot.id)

    def test_predecessor_is_checked_before_early_start_rule(self):
        self.slot.plan_start = datetime.now() + timedelta(hours=1)
        self.slot.plan_end = datetime.now() + timedelta(hours=2)
        self.db.commit()

        with self.assertRaisesRegex(TaskExecutionInvalidError, "方法开发.*尚未完成"):
            start_task_execution(self.db, self.slot.id)

    def test_started_task_cannot_start_twice(self):
        self.predecessor.status = "done"
        self.slot.actual_start = datetime.now() - timedelta(minutes=10)
        self.slot.status = "running"
        self.task.status = "running"
        self.db.commit()

        with self.assertRaisesRegex(TaskExecutionInvalidError, "已经开始"):
            start_task_execution(self.db, self.slot.id)

    def test_start_repairs_stale_running_task_with_paused_slots(self):
        self.predecessor.status = "done"
        self.task.status = "running"
        self.slot.status = "paused"
        self.slot.actual_start = datetime.now() - timedelta(hours=1)
        self.slot.actual_end = datetime.now() - timedelta(minutes=10)
        self.db.commit()

        start_task_execution(self.db, self.slot.id)

        self.assertEqual("running", self.task.status)
        self.assertEqual("running", self.slot.status)
        self.assertIsNotNone(self.slot.actual_start)
        self.assertIsNone(self.slot.actual_end)

    def test_start_ignores_superseded_open_slot_when_reconciling_running_state(self):
        self.predecessor.status = "done"
        self.task.status = "running"
        self.slot.status = "paused"
        stale_slot = TimeSlot(
            task_id=self.task.id,
            instrument_id=self.slot.instrument_id,
            plan_start=datetime.now() - timedelta(hours=2),
            plan_end=datetime.now() - timedelta(hours=1),
            actual_start=datetime.now() - timedelta(hours=2),
            status="running",
            lifecycle_status="superseded",
            tier="confirmed",
        )
        self.db.add(stale_slot)
        self.db.commit()

        start_task_execution(self.db, self.slot.id)

        self.assertEqual("running", self.task.status)
        self.assertEqual("running", self.slot.status)
        self.assertEqual("superseded", stale_slot.lifecycle_status)

    def test_zero_length_previous_paused_slot_does_not_block_resume(self):
        self.predecessor.status = "done"
        self.task.requires_instrument = True
        self.slot.plan_start = datetime.now()
        self.slot.plan_end = self.slot.plan_start + timedelta(hours=1)
        previous_task = Task(
            id=3, project_id=1, name="前序暂停任务", task_type="FFKF_001", status="paused",
            requires_instrument=True,
        )
        previous_slot = TimeSlot(
            id=3, task_id=3, instrument_id=1,
            plan_start=self.slot.plan_start - timedelta(minutes=5),
            plan_end=self.slot.plan_start,
            status="paused", tier="confirmed",
        )
        self.db.add_all([previous_task, previous_slot])
        self.db.commit()

        start_task_execution(self.db, self.slot.id, allow_queue_insert=False)

        self.assertEqual("running", self.task.status)

    def test_paused_task_cannot_resume_from_ended_slot(self):
        self.predecessor.status = "done"
        self.task.status = "paused"
        self.slot.status = "paused"
        self.slot.plan_start = datetime.now() - timedelta(hours=2)
        self.slot.plan_end = datetime.now() - timedelta(hours=1)
        self.slot.actual_start = self.slot.plan_start
        self.slot.actual_end = self.slot.plan_end
        self.db.commit()

        with self.assertRaisesRegex(TaskExecutionInvalidError, "没有可恢复的未来活动时间槽"):
            start_task_execution(self.db, self.slot.id)


if __name__ == "__main__":
    unittest.main()
