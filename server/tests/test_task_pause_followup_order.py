import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.errors import DomainConflictError
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot, User
from app.services.task_pause_service import pause_and_switch_task
from app.services.task_pause_solver_service import replan_pause_switch
from app.services.task_pause_switch_context_service import build_pause_switch_context
from app.services.task_execution_service import start_task_execution
from app.services.schedule_conflict_service import find_instrument_conflicts


class TaskPauseFollowupOrderTest(unittest.TestCase):
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

    def tearDown(self):
        self.db.close()

    def test_source_continuous_successor_precedes_ordinary_queue_after_switch(self):
        source_parent, source, source_followup = self._task_group(self.project_a, "A")
        _, target, _ = self._task_group(self.project_b, "B")
        ordinary = Task(
            project_id=self.project_a.id,
            name="普通队列任务",
            task_type="FFYZ_001",
            requires_instrument=True,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=2,
        )
        self.db.add(ordinary)
        self.db.flush()
        now = datetime.now().replace(second=0, microsecond=0)
        source_slot = self._slot(source, now - timedelta(hours=1), now + timedelta(hours=3))
        target_slot = self._slot(target, now + timedelta(hours=3), now + timedelta(hours=6))
        self._slot(source_followup, now + timedelta(hours=6), now + timedelta(hours=8), False)
        self._slot(ordinary, now + timedelta(hours=8), now + timedelta(hours=10))
        self.db.add(TaskDependency(
            task_id=source_followup.id,
            predecessor_id=source.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()
        start_task_execution(self.db, source_slot.id, self.operator.id)
        self.db.commit()

        pause_and_switch_task(
            self.db, source_slot.id, "切换项目", self.operator, target_slot.id,
        )
        self.db.commit()

        source_end = self._active_slots(source)[-1].plan_end
        followup_slots = self._active_slots(source_followup)
        ordinary_slots = self._active_slots(ordinary)
        self.assertLessEqual(source_end, followup_slots[0].plan_start)
        self.assertLessEqual(followup_slots[-1].plan_end, ordinary_slots[0].plan_start)
        self.assertEqual(source_parent.id, source_followup.parent_id)

    def test_switch_context_exposes_bounded_solver_input_in_queue_order(self):
        source_parent, source, source_followup = self._task_group(self.project_a, "A")
        _, target, target_followup = self._task_group(self.project_b, "B")
        now = datetime.now().replace(second=0, microsecond=0)
        source_slot = self._slot(source, now - timedelta(hours=1), now + timedelta(hours=2))
        target_slot = self._slot(target, now + timedelta(hours=2), now + timedelta(hours=5))
        self._slot(target_followup, now + timedelta(hours=5), now + timedelta(hours=6), False)
        self._slot(source_followup, now + timedelta(hours=6), now + timedelta(hours=8), False)
        self.db.add_all([
            TaskDependency(task_id=target_followup.id, predecessor_id=target.id, dependency_type="continuous_successor"),
            TaskDependency(task_id=source_followup.id, predecessor_id=source.id, dependency_type="continuous_successor"),
        ])
        self.db.commit()

        context = build_pause_switch_context(self.db, source_slot, target_slot, now)

        self.assertEqual(
            [target.id, target_followup.id, source.id, source_followup.id],
            [entry.task.id for entry in context.queue],
        )
        self.assertEqual(
            [(source.id, target.id), (source.id, target_followup.id)],
            context.queue_dependencies,
        )
        self.assertEqual(source.id, context.paused_source_task_id)
        self.assertEqual(set(context.remaining_duration_minutes), context.task_ids)
        self.assertEqual(source_parent.id, source_followup.parent_id)

    def test_manual_followup_does_not_block_different_assignee_instrument_task(self):
        _, target, target_followup = self._task_group(self.project_b, "B")
        source_parent, source, _ = self._task_group(self.project_a, "A")
        other_operator = User(username="other-tech", display_name="其他技术员", role="技术员")
        self.db.add(other_operator)
        self.db.flush()
        source.assignee_id = other_operator.id
        now = datetime.now().replace(second=0, microsecond=0)
        source_slot = self._slot(source, now - timedelta(hours=1), now + timedelta(hours=2))
        target_slot = self._slot(target, now + timedelta(hours=2), now + timedelta(hours=5))
        self._slot(target_followup, now + timedelta(hours=5), now + timedelta(hours=6), False)
        self.db.add(TaskDependency(
            task_id=target_followup.id,
            predecessor_id=target.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()

        context = build_pause_switch_context(self.db, source_slot, target_slot, now)

        # 人工任务不进仪器队列锁；但目标连续后续链仍必须在暂停任务恢复前完成。
        self.assertEqual(
            [(source.id, target.id), (source.id, target_followup.id)],
            context.queue_dependencies,
        )
        self.assertEqual(source_parent.id, source.parent_id)

    def test_switch_context_includes_target_assignee_slots_when_source_is_nonhuman(self):
        source = Task(
            project_id=self.project_a.id,
            name="无需人员的检测",
            task_type="CHECK_001",
            requires_instrument=True,
            requires_human=False,
            status="scheduled",
            est_duration_hours=2,
        )
        _, target, _ = self._task_group(self.project_b, "B")
        operator_task = Task(
            project_id=self.project_b.id,
            name="切入任务负责人的非仪器任务",
            task_type="DOC_001",
            requires_instrument=False,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=1,
        )
        queue_tail = Task(
            project_id=self.project_b.id,
            name="仪器队列末尾任务",
            task_type="CHECK_002",
            requires_instrument=True,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=1,
        )
        self.db.add_all([source, operator_task, queue_tail])
        self.db.flush()
        now = datetime.now().replace(second=0, microsecond=0)
        source_slot = self._slot(source, now - timedelta(hours=1), now + timedelta(hours=1))
        target_slot = self._slot(target, now + timedelta(hours=1), now + timedelta(hours=3))
        self._slot(operator_task, now + timedelta(hours=3), now + timedelta(hours=4), False)
        self._slot(queue_tail, now + timedelta(hours=4), now + timedelta(hours=5))
        self.db.commit()

        context = build_pause_switch_context(self.db, source_slot, target_slot, now)

        self.assertIn(operator_task.id, context.task_ids)

    def test_switch_context_excludes_cross_parent_continuous_successor(self):
        _, target, _ = self._task_group(self.project_b, "B")
        other_parent = Task(project_id=self.project_b.id, name="另一个标准计划", task_type="ROOT")
        self.db.add(other_parent)
        self.db.flush()
        invalid_followup = Task(
            project_id=self.project_b.id, parent_id=other_parent.id,
            name="错误跨组方案撰写", task_type="QCFA_001",
            requires_human=True, assignee_id=self.operator.id, status="scheduled",
        )
        self.db.add(invalid_followup)
        self.db.flush()
        now = datetime.now().replace(second=0, microsecond=0)
        source_slot = self._slot(target, now - timedelta(hours=1), now + timedelta(hours=2))
        target_slot = self._slot(target, now + timedelta(hours=2), now + timedelta(hours=5))
        self._slot(invalid_followup, now + timedelta(hours=5), now + timedelta(hours=6), False)
        self.db.add(TaskDependency(
            task_id=invalid_followup.id, predecessor_id=target.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()

        context = build_pause_switch_context(self.db, source_slot, target_slot, now)

        self.assertNotIn(invalid_followup.id, context.task_ids)

    def test_switch_window_conflict_rolls_back_anchors_and_slots(self):
        _, source, _ = self._task_group(self.project_a, "A")
        _, target, _ = self._task_group(self.project_b, "B")
        now = datetime.now().replace(second=0, microsecond=0)
        source_start = now - timedelta(hours=1)
        source_end = now + timedelta(hours=2)
        target_start = source_end
        target_end = source_end + timedelta(hours=3)
        source_slot = self._slot(source, source_start, source_end)
        target_slot = self._slot(target, target_start, target_end)
        self.db.commit()

        with patch(
            "app.services.task_pause_solver_service.replan_resource_closure",
            return_value={
                "status": "error",
                "message": "受限重排窗口与窗口外任务发生资源冲突",
            },
        ):
            with self.assertRaises(DomainConflictError) as raised:
                replan_pause_switch(self.db, source_slot, target_slot, now)

        # 求解器没给出 INFEASIBLE 这个证明，就不能断言"改日期解决不了"——
        # 没有结论时如实说没有结论。
        failure = raised.exception.detail["pause_switch_failure"]
        self.assertEqual("undetermined", failure["kind"])
        self.assertEqual([], failure["overruns"])
        # 求解器的原话必须留着，它是排查这类冲突唯一的线索。
        self.assertEqual("受限重排窗口与窗口外任务发生资源冲突", failure["solver_message"])

        self.db.refresh(source_slot)
        self.db.refresh(target_slot)
        self.db.refresh(source)
        self.assertEqual(source_start, source_slot.plan_start)
        self.assertEqual(source_end, source_slot.plan_end)
        self.assertIsNone(source_slot.actual_end)
        self.assertEqual("active", source_slot.lifecycle_status)
        self.assertEqual("scheduled", source.status)
        self.assertEqual(target_start, target_slot.plan_start)
        self.assertEqual(target_end, target_slot.plan_end)
        self.assertEqual("active", target_slot.lifecycle_status)

    def test_zero_length_pause_anchor_is_not_an_instrument_conflict(self):
        _, source, _ = self._task_group(self.project_a, "A")
        _, target, _ = self._task_group(self.project_b, "B")
        now = datetime.now().replace(second=0, microsecond=0)
        self._slot(source, now, now)
        self._slot(target, now, now + timedelta(hours=1))
        self.db.commit()

        self.assertEqual([], find_instrument_conflicts(self.db))

    def test_start_marks_only_requested_slot_running(self):
        _, task, _ = self._task_group(self.project_a, "A")
        now = datetime.now().replace(second=0, microsecond=0)
        current = self._slot(task, now, now + timedelta(hours=1))
        future = self._slot(task, now + timedelta(hours=2), now + timedelta(hours=3))
        self.db.commit()

        start_task_execution(self.db, current.id, self.operator.id)

        self.assertEqual("running", current.status)
        self.assertIsNotNone(current.actual_start)
        self.assertEqual("scheduled", future.status)
        self.assertIsNone(future.actual_start)

    def test_manual_task_without_predecessor_in_closure_is_not_pinned(self):
        """前驱不在闭包里的人工任务不能被钉在原位。

        线上就是这么排不下的：一个方案撰写，它的方法开发早就做完、不在闭包里，
        却因为队列锁被硬钉在两个不相干项目的仪器任务中间，把四个项目串成一条链，
        切换随即判定不可行——而报出来的原因是"某某项目要延期"，看不出真正的症结。
        """
        _, target, _ = self._task_group(self.project_b, "B")
        _, source, _ = self._task_group(self.project_a, "A")
        project_c = Project(code="C", name="项目C")
        self.db.add(project_c)
        self.db.flush()
        stranded_parent, stranded_method, stranded = self._task_group(project_c, "C")
        # 项目C 的方法开发已经做完，闭包里只剩下它的方案撰写。
        stranded_method.status = "completed"
        now = datetime.now().replace(second=0, microsecond=0)
        source_slot = self._slot(source, now - timedelta(hours=1), now + timedelta(hours=2))
        target_slot = self._slot(target, now + timedelta(hours=2), now + timedelta(hours=8))
        # 落在仪器队列窗口之内，才会被闭包按"同负责人的人工任务"拉进来。
        self._slot(stranded, now + timedelta(hours=4), now + timedelta(hours=6), False)
        self.db.add(TaskDependency(
            task_id=stranded.id,
            predecessor_id=stranded_method.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()

        context = build_pause_switch_context(self.db, source_slot, target_slot, now)

        self.assertIn(stranded.id, context.task_ids)
        locked = {task_id for edge in context.queue_dependencies for task_id in edge}
        self.assertNotIn(stranded.id, locked)
        # 仪器上的排队仍然锁着：接替任务在前，被暂停的源任务在后。
        self.assertEqual([(source.id, target.id)], context.queue_dependencies)
        self.assertEqual(stranded_parent.id, stranded.parent_id)

    def _task_group(self, project: Project, suffix: str) -> tuple[Task, Task, Task]:
        parent = Task(project_id=project.id, name=f"标准计划{suffix}", task_type="ROOT")
        self.db.add(parent)
        self.db.flush()
        method = Task(
            project_id=project.id,
            parent_id=parent.id,
            name=f"方法开发{suffix}",
            task_type="FFKF_001",
            requires_instrument=True,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=3,
        )
        followup = Task(
            project_id=project.id,
            parent_id=parent.id,
            name=f"方案撰写{suffix}",
            task_type="QCFA_001",
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=2,
        )
        self.db.add_all([method, followup])
        self.db.flush()
        return parent, method, followup

    def _slot(
        self,
        task: Task,
        start: datetime,
        end: datetime,
        uses_instrument: bool = True,
    ) -> TimeSlot:
        slot = TimeSlot(
            task_id=task.id,
            instrument_id=self.instrument.id if uses_instrument else None,
            plan_start=start,
            plan_end=end,
            status="scheduled",
            tier="confirmed",
        )
        self.db.add(slot)
        return slot

    def _active_slots(self, task: Task) -> list[TimeSlot]:
        return self.db.query(TimeSlot).filter(
            TimeSlot.task_id == task.id,
            TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.plan_start, TimeSlot.id).all()


if __name__ == "__main__":
    unittest.main()
