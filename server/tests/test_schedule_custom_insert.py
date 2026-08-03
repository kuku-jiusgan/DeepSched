import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot, User
from app.services.schedule_insert_service import (
    ScheduleInsertInvalidError,
    _build_custom_insert_context,
    _insert_audit_detail,
    _insert_notification_reason,
)


class ScheduleCustomInsertTest(unittest.TestCase):
    def test_insert_notification_reason_identifies_operator(self):
        self.assertEqual(
            "插单导致（操作人：系统管理员）",
            _insert_notification_reason("系统管理员"),
        )

    def test_insert_audit_detail_names_inserted_and_anchor_tasks(self):
        source = self._task(self.source_project, "方法开发")
        anchor = self._task(self.target_project, "方案撰写")

        detail = _insert_audit_detail([source], anchor, 3, "run-001")

        self.assertEqual(
            "将【SRC · 项目SRC · 方法开发】插入到【TGT · 项目TGT · 方案撰写】之后",
            detail["insert_summary"],
        )
        self.assertEqual(3, detail["moved_tasks"])
        self.assertEqual("run-001", detail["schedule_run_id"])

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.source_project = self._project("SRC")
        self.target_project = self._project("TGT")

    def tearDown(self):
        self.db.close()

    def _project(self, code: str) -> Project:
        project = Project(
            code=code,
            name=f"项目{code}",
            priority=3,
            end_date=datetime(2026, 8, 31, 18, 0),
        )
        self.db.add(project)
        self.db.flush()
        return project

    def _task(self, project: Project, name: str, status: str = "scheduled") -> Task:
        task = Task(
            project_id=project.id,
            name=name,
            task_type="test",
            status=status,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _schedule(self, task: Task) -> None:
        self.db.add(TimeSlot(
            task_id=task.id,
            plan_start=datetime(2026, 8, 3, 8, 30),
            plan_end=datetime(2026, 8, 3, 12, 30),
            tier="confirmed",
            status="scheduled",
        ))
        self.db.flush()

    def test_custom_insert_uses_temporary_order_without_changing_business_dependencies(self):
        anchor = self._task(self.target_project, "目标任务")
        target_next = self._task(self.target_project, "目标后续")
        source_first = self._task(self.source_project, "插入任务一")
        source_last = self._task(self.source_project, "插入任务二")
        self.db.add_all([
            TaskDependency(task_id=target_next.id, predecessor_id=anchor.id),
            TaskDependency(task_id=source_last.id, predecessor_id=source_first.id),
        ])
        self._schedule(anchor)
        self._schedule(target_next)
        self._schedule(source_first)
        self._schedule(source_last)

        context = _build_custom_insert_context(
            self.db,
            anchor.id,
            [source_first, source_last],
        )

        pairs = {
            (item.task_id, item.predecessor_id)
            for item in self.db.query(TaskDependency).all()
        }
        self.assertIn((source_first.id, anchor.id), context["dependency_pairs"])
        self.assertIn((target_next.id, source_last.id), context["dependency_pairs"])
        self.assertNotIn((source_first.id, anchor.id), pairs)
        self.assertNotIn((target_next.id, source_last.id), pairs)
        self.assertEqual({
            (target_next.id, anchor.id),
            (source_last.id, source_first.id),
        }, pairs)
        self.assertEqual("inserted", context["impact_roles"][source_first.id])
        self.assertEqual("anchor_downstream", context["impact_roles"][target_next.id])

    def test_custom_insert_does_not_add_unselected_source_downstream_to_insert_block(self):
        anchor = self._task(self.target_project, "目标方法开发")
        target_next = self._task(self.target_project, "目标后续")
        source = self._task(self.source_project, "方法开发")
        source_next = self._task(self.source_project, "验证")
        self.db.add_all([
            TaskDependency(task_id=target_next.id, predecessor_id=anchor.id),
            TaskDependency(task_id=source_next.id, predecessor_id=source.id),
        ])
        self._schedule(anchor)
        self._schedule(source)
        self._schedule(source_next)

        context = _build_custom_insert_context(self.db, anchor.id, [source])

        self.assertIn((source.id, anchor.id), context["dependency_pairs"])
        self.assertIn((target_next.id, source.id), context["dependency_pairs"])
        self.assertNotIn((target_next.id, source_next.id), context["dependency_pairs"])
        self.assertIn(source_next.id, {task.id for task in context["replan_tasks"]})

    def test_custom_insert_rejects_cycle(self):
        anchor = self._task(self.target_project, "目标任务")
        source = self._task(self.source_project, "插入任务")
        self.db.add(TaskDependency(task_id=anchor.id, predecessor_id=source.id))
        self._schedule(anchor)
        self._schedule(source)

        with self.assertRaisesRegex(ScheduleInsertInvalidError, "循环前置关系"):
            _build_custom_insert_context(self.db, anchor.id, [source])

    def test_repeated_custom_insert_accepts_existing_dependencies(self):
        anchor = self._task(self.target_project, "目标任务")
        source = self._task(self.source_project, "插入任务")
        self.db.add(TaskDependency(task_id=source.id, predecessor_id=anchor.id))
        self._schedule(anchor)
        self._schedule(source)
        self.db.flush()

        context = _build_custom_insert_context(self.db, anchor.id, [source])

        self.assertIn((source.id, anchor.id), context["dependency_pairs"])

    def test_custom_insert_rejects_completed_anchor_downstream(self):
        anchor = self._task(self.target_project, "目标任务")
        target_next = self._task(self.target_project, "已完成后续", status="completed")
        source = self._task(self.source_project, "插入任务")
        self.db.add(TaskDependency(task_id=target_next.id, predecessor_id=anchor.id))
        self._schedule(anchor)
        self._schedule(source)

        with self.assertRaisesRegex(ScheduleInsertInvalidError, "已开始或已完成"):
            _build_custom_insert_context(self.db, anchor.id, [source])

    def test_custom_insert_rejects_selected_unscheduled_task(self):
        anchor = self._task(self.target_project, "目标方法开发")
        source = self._task(self.source_project, "方法验证", status="waiting_external")
        self._schedule(anchor)

        with self.assertRaisesRegex(ScheduleInsertInvalidError, "只能选择已经生成排程"):
            _build_custom_insert_context(self.db, anchor.id, [source])

    def test_custom_insert_ignores_unscheduled_tasks_after_approval_gate(self):
        anchor = self._task(self.target_project, "目标方法开发")
        source = self._task(self.source_project, "方法开发")
        gate = self._task(self.source_project, "方案签批", status="waiting_external")
        gate.is_external_gate = True
        validation = self._task(self.source_project, "方法验证", status="waiting_external")
        report = self._task(self.source_project, "报告撰写", status="waiting_external")
        self.db.add_all([
            TaskDependency(task_id=gate.id, predecessor_id=source.id),
            TaskDependency(task_id=validation.id, predecessor_id=gate.id),
            TaskDependency(task_id=report.id, predecessor_id=validation.id),
        ])
        self._schedule(anchor)
        self._schedule(source)

        context = _build_custom_insert_context(self.db, anchor.id, [source])

        replan_ids = {task.id for task in context["replan_tasks"]}
        self.assertIn(source.id, replan_ids)
        self.assertNotIn(validation.id, replan_ids)
        self.assertNotIn(report.id, replan_ids)

    def test_custom_insert_pushes_movable_resource_queue_after_inserted_task(self):
        owner = User(username="owner", display_name="负责人", role="分析员")
        instrument = Instrument(id=1, code="INST-1", name="测试仪器")
        self.db.add_all([owner, instrument])
        self.db.flush()
        anchor = self._task(self.target_project, "目标任务")
        source = self._task(self.source_project, "插单任务")
        source.requires_instrument = True
        source.requires_human = True
        source.instrument_ids = [instrument.id]
        source.assignee_id = owner.id
        queued = self._task(self.target_project, "资源后续任务")
        queued.requires_instrument = True
        queued.requires_human = True
        queued.instrument_ids = [instrument.id]
        queued.assignee_id = owner.id
        self._schedule(anchor)
        self._schedule(source)
        self.db.add(TimeSlot(
            task_id=queued.id,
            instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 3, 13, 0),
            plan_end=datetime(2026, 8, 3, 17, 0),
            tier="confirmed",
            status="scheduled",
        ))
        self.db.flush()

        context = _build_custom_insert_context(self.db, anchor.id, [source])

        self.assertIn(queued.id, {task.id for task in context["replan_tasks"]})
        self.assertIn((queued.id, source.id), context["dependency_pairs"])
        self.assertEqual("shifted", context["impact_roles"][queued.id])

    def test_custom_insert_pushes_frozen_resource_task_after_inserted_task(self):
        instrument = Instrument(id=1, code="INST-1", name="测试仪器")
        self.db.add(instrument)
        anchor = self._task(self.target_project, "目标任务")
        source = self._task(self.source_project, "插单任务")
        source.requires_instrument = True
        source.instrument_ids = [instrument.id]
        frozen = self._task(self.target_project, "冻结任务")
        frozen.requires_instrument = True
        frozen.instrument_ids = [instrument.id]
        self._schedule(anchor)
        self._schedule(source)
        self.db.add(TimeSlot(
            task_id=frozen.id,
            instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 3, 13, 0),
            plan_end=datetime(2026, 8, 3, 17, 0),
            tier="frozen",
            status="scheduled",
        ))
        self.db.flush()

        context = _build_custom_insert_context(self.db, anchor.id, [source])

        self.assertIn(frozen.id, {task.id for task in context["replan_tasks"]})
        self.assertIn((frozen.id, source.id), context["dependency_pairs"])

    def test_custom_insert_allows_frozen_selected_task(self):
        anchor = self._task(self.target_project, "目标任务")
        source = self._task(self.source_project, "冻结插单任务")
        self._schedule(anchor)
        self._schedule(source)
        source_slot = self.db.query(TimeSlot).filter(TimeSlot.task_id == source.id).one()
        source_slot.tier = "frozen"
        self.db.flush()

        context = _build_custom_insert_context(self.db, anchor.id, [source])

        self.assertIn(source.id, {task.id for task in context["replan_tasks"]})
        self.assertIn((source.id, anchor.id), context["dependency_pairs"])


if __name__ == "__main__":
    unittest.main()
