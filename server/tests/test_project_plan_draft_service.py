import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.api.projects import _task_to_out
from app.models import (
    AuditLog,
    Project,
    Task,
    TaskDependency,
    TaskExecutionSegment,
    TaskNightRun,
    TaskTypeConfig,
    TimeSlot,
    User,
)
from app.schemas.schemas import TaskUpdate
from app.schemas.project_plan_draft_schemas import ProjectPlanDraftCommitIn, ProjectPlanDraftTaskIn
from app.services.project_plan_draft_service import (
    ProjectPlanDraftInvalidError,
    commit_project_plan_drafts,
)
from app.services.project_plan_change_service import delete_task_plan, update_task_plan


class ProjectPlanDraftServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.manager = User(id=1, username="manager", display_name="负责人", role="分析员")
        self.gate_owner = User(id=2, username="gate-owner", display_name="签批负责人", role="分析员")
        self.project = Project(id=1, name="草稿项目", code="DRAFT-1", estimated_hours=100, manager_id=1)
        self.db.add_all([self.manager, self.gate_owner, self.project])
        for code in ["FFKF_001", "QCFA_001", "FFYZ_001", "ZXBG_001"]:
            self.db.add(TaskTypeConfig(name=code, code=code, resource_type="both", is_active=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_commit_maps_client_ids_and_preserves_approval_restriction(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "方法开发", "FFKF_001", 70),
            self._task(-2, "方案撰写", "QCFA_001", 5, predecessors=[-1]),
            self._task(-3, "方案签批", "approval_gate", None, predecessors=[-2], is_gate=True),
            self._task(-4, "方法验证", "FFYZ_001", 20, predecessors=[-3]),
            self._task(-5, "报告撰写", "ZXBG_001", 5, predecessors=[-4]),
        ])

        result = commit_project_plan_drafts(self.db, 1, data, self.manager)

        tasks = self.db.query(Task).filter(Task.project_id == 1).all()
        by_name = {task.name: task for task in tasks}
        dependencies = {
            (item.predecessor_id, item.task_id)
            for item in self.db.query(TaskDependency).all()
        }
        self.assertEqual(5, result.created)
        self.assertEqual("waiting_external", by_name["方法验证"].status)
        self.assertEqual("waiting_external", by_name["报告撰写"].status)
        self.assertTrue(by_name["方案签批"].is_external_gate)
        self.assertEqual(self.project.manager_id, by_name["方案签批"].assignee_id)
        saved_gate = _task_to_out(by_name["方案签批"], self.db)
        self.assertTrue(saved_gate.is_external_gate)
        self.assertEqual("approval_gate", saved_gate.task_type)
        self.assertEqual("not_submitted", saved_gate.gate_status)
        self.assertIn((by_name["方案签批"].id, by_name["方法验证"].id), dependencies)
        audit = self.db.query(AuditLog).filter(
            AuditLog.action == "project_plan_drafts_committed"
        ).one()
        self.assertEqual("DRAFT-1 · 草稿项目", audit.detail["target_display"])
        self.assertNotIn("client_ids", audit.detail)
        self.assertEqual("方法开发", audit.detail["task_details"][0]["name"])
        self.assertEqual(70, audit.detail["task_details"][0]["estimated_hours"])
        self.assertEqual(["方法开发"], audit.detail["task_details"][1]["predecessors"])
        self.assertEqual(0, self.db.query(TimeSlot).count())

    def test_commit_preserves_selected_approval_gate_assignee(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "方案撰写", "QCFA_001", 5),
            self._task(-2, "方案签批", "approval_gate", None, predecessors=[-1], is_gate=True, assignee_id=2),
            self._task(-3, "方法验证", "FFYZ_001", 20, predecessors=[-2]),
        ])

        commit_project_plan_drafts(self.db, 1, data, self.manager)

        saved_gate = self.db.query(Task).filter(Task.is_external_gate.is_(True)).one()
        self.assertEqual(self.gate_owner.id, saved_gate.assignee_id)

    def test_saved_approval_gate_assignee_can_be_updated(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "方案撰写", "QCFA_001", 5),
            self._task(-2, "方案签批", "approval_gate", None, predecessors=[-1], is_gate=True),
            self._task(-3, "方法验证", "FFYZ_001", 20, predecessors=[-2]),
        ])
        commit_project_plan_drafts(self.db, 1, data, self.manager)
        saved_gate = self.db.query(Task).filter(Task.is_external_gate.is_(True)).one()

        updated = update_task_plan(
            self.db,
            saved_gate.id,
            TaskUpdate(name="客户方案签批", assignee_id=self.gate_owner.id),
        )

        self.assertEqual("客户方案签批", updated.name)
        self.assertEqual(self.gate_owner.id, updated.assignee_id)

    def test_task_plan_edit_preserves_continuous_successor_dependency_type(self):
        parent = Task(
            project_id=self.project.id, name="标准计划", task_type="group",
        )
        method = Task(
            project_id=self.project.id, parent=parent, name="方法开发",
            task_type="FFKF_001", status="scheduled",
        )
        scheme = Task(
            project_id=self.project.id, parent=parent, name="方案撰写",
            task_type="QCFA_001", status="scheduled",
        )
        self.db.add_all([parent, method, scheme])
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=scheme.id, predecessor_id=method.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()

        update_task_plan(
            self.db, scheme.id, TaskUpdate(predecessor_ids=[method.id]),
        )

        dependency = self.db.query(TaskDependency).filter(
            TaskDependency.task_id == scheme.id,
            TaskDependency.predecessor_id == method.id,
        ).one()
        self.assertEqual("continuous_successor", dependency.dependency_type)

    def test_task_plan_edit_downgrades_continuous_successor_after_parent_change(self):
        parent = Task(project_id=self.project.id, name="标准计划", task_type="group")
        other_parent = Task(project_id=self.project.id, name="另一个标准计划", task_type="group")
        method = Task(
            project_id=self.project.id, parent=parent, name="方法开发",
            task_type="FFKF_001", status="scheduled",
        )
        scheme = Task(
            project_id=self.project.id, parent=parent, name="方案撰写",
            task_type="QCFA_001", status="scheduled",
        )
        self.db.add_all([parent, other_parent, method, scheme])
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=scheme.id, predecessor_id=method.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()

        update_task_plan(
            self.db, scheme.id, TaskUpdate(parent_id=other_parent.id, predecessor_ids=[method.id]),
        )

        dependency = self.db.query(TaskDependency).filter(
            TaskDependency.task_id == scheme.id,
            TaskDependency.predecessor_id == method.id,
        ).one()
        self.assertEqual("predecessor", dependency.dependency_type)

    def test_hours_over_project_limit_rolls_back_whole_batch(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "超限任务", "FFKF_001", 101),
        ])

        with self.assertRaises(ProjectPlanDraftInvalidError):
            commit_project_plan_drafts(self.db, 1, data, self.manager)

        self.assertEqual(0, self.db.query(Task).count())

    def test_missing_client_reference_is_rejected_before_insert(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "错误依赖", "FFKF_001", 10, predecessors=[-99]),
        ])

        with self.assertRaises(ProjectPlanDraftInvalidError):
            commit_project_plan_drafts(self.db, 1, data, self.manager)

        self.assertEqual(0, self.db.query(Task).count())

    def test_self_dependency_is_rejected_before_insert(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "自依赖任务", "QCFA_001", 10, predecessors=[-1]),
        ])

        with self.assertRaisesRegex(ProjectPlanDraftInvalidError, "前置关系不能形成循环"):
            commit_project_plan_drafts(self.db, 1, data, self.manager)

        self.assertEqual(0, self.db.query(Task).count())

    def test_dependency_cycle_is_rejected_before_insert(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "任务 A", "QCFA_001", 10, predecessors=[-2]),
            self._task(-2, "任务 B", "QCFA_001", 10, predecessors=[-1]),
        ])

        with self.assertRaisesRegex(ProjectPlanDraftInvalidError, "前置关系不能形成循环"):
            commit_project_plan_drafts(self.db, 1, data, self.manager)

        self.assertEqual(0, self.db.query(Task).count())

    def test_parent_cycle_is_rejected_before_insert(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "父任务 A", "QCFA_001", 10, parent_id=-2),
            self._task(-2, "父任务 B", "QCFA_001", 10, parent_id=-1),
        ])

        with self.assertRaisesRegex(ProjectPlanDraftInvalidError, "父子任务层级不能形成循环"):
            commit_project_plan_drafts(self.db, 1, data, self.manager)

        self.assertEqual(0, self.db.query(Task).count())

    def test_method_task_without_instrument_is_rejected(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "方法开发", "FFKF_001", 10, instrument_ids=[]),
        ])

        with self.assertRaisesRegex(ProjectPlanDraftInvalidError, "必须指定仪器"):
            commit_project_plan_drafts(self.db, 1, data, self.manager)

        self.assertEqual(0, self.db.query(Task).count())

    def test_method_parent_uses_child_instruments(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(
                -1,
                "方法开发",
                "FFKF_001",
                30,
                instrument_ids=[],
            ),
            self._task(
                -2,
                "LCMS方法开发",
                "FFKF_001",
                10,
                parent_id=-1,
                instrument_ids=[1],
            ),
            self._task(
                -3,
                "GCMS方法开发",
                "FFKF_001",
                20,
                parent_id=-1,
                instrument_ids=[2],
            ),
        ])

        commit_project_plan_drafts(self.db, 1, data, self.manager)

        tasks = {
            task.name: task
            for task in self.db.query(Task).filter(Task.project_id == 1).all()
        }
        parent = tasks["方法开发"]
        self.assertEqual("group", parent.task_type)
        self.assertFalse(parent.requires_instrument)
        self.assertFalse(parent.requires_human)
        self.assertEqual([], parent.instrument_ids)
        self.assertIsNone(parent.assignee_id)
        self.assertEqual(parent.id, tasks["LCMS方法开发"].parent_id)
        self.assertEqual(parent.id, tasks["GCMS方法开发"].parent_id)

    def test_deleting_saved_approval_gate_restores_plan_chain(self):
        data = ProjectPlanDraftCommitIn(tasks=[
            self._task(-1, "方案撰写", "QCFA_001", 5),
            self._task(-2, "方案签批", "approval_gate", None, predecessors=[-1], is_gate=True),
            self._task(-3, "方法验证", "FFYZ_001", 20, predecessors=[-2]),
            self._task(-4, "报告撰写", "ZXBG_001", 5, predecessors=[-3]),
        ])
        commit_project_plan_drafts(self.db, 1, data, self.manager)
        tasks = {task.name: task for task in self.db.query(Task).filter(Task.project_id == 1).all()}

        delete_task_plan(self.db, tasks["方案签批"].id)

        dependencies = {
            (item.predecessor_id, item.task_id)
            for item in self.db.query(TaskDependency).all()
        }
        self.assertIsNone(self.db.get(Task, tasks["方案签批"].id))
        self.assertIn((tasks["方案撰写"].id, tasks["方法验证"].id), dependencies)
        self.assertEqual("pending", self.db.get(Task, tasks["方法验证"].id).status)
        self.assertTrue(self.db.get(Task, tasks["报告撰写"].id).schedule_dirty)

    def test_admin_deletes_completed_task_tree_and_execution_history(self):
        parent = Task(
            project_id=1, name="标准计划", task_type="group", status="pending",
        )
        child = Task(
            project_id=1, parent=parent, name="方法开发",
            task_type="FFKF_001", status="completed",
        )
        self.db.add_all([parent, child])
        self.db.flush()
        slot = TimeSlot(
            task_id=child.id, plan_start=datetime(2026, 8, 21, 8, 30),
            plan_end=datetime(2026, 8, 21, 10, 0),
            actual_start=datetime(2026, 8, 21, 8, 30),
            actual_end=datetime(2026, 8, 21, 9, 30),
            status="completed",
        )
        self.db.add(slot)
        self.db.flush()
        self.db.add(TaskExecutionSegment(
            task_id=child.id, slot_id=slot.id,
            started_at=slot.actual_start, ended_at=slot.actual_end,
        ))
        self.db.add(TaskNightRun(
            task_id=child.id, slot_id=slot.id, instrument_id=1,
            started_at=slot.actual_start, ended_at=slot.actual_end,
        ))
        self.db.commit()

        delete_task_plan(self.db, parent.id, allow_completed=True)

        self.assertEqual(0, self.db.query(Task).filter(Task.id.in_([parent.id, child.id])).count())
        self.assertEqual(0, self.db.query(TimeSlot).filter(TimeSlot.task_id == child.id).count())
        self.assertEqual(0, self.db.query(TaskExecutionSegment).filter(TaskExecutionSegment.task_id == child.id).count())
        self.assertEqual(0, self.db.query(TaskNightRun).filter(TaskNightRun.task_id == child.id).count())

    def _task(
        self,
        client_id: int,
        name: str,
        task_type: str,
        hours: float | None,
        predecessors: list[int] | None = None,
        is_gate: bool = False,
        instrument_ids: list[int] | None = None,
        parent_id: int | None = None,
        assignee_id: int | None = None,
    ) -> ProjectPlanDraftTaskIn:
        return ProjectPlanDraftTaskIn(
            client_id=client_id,
            name=name,
            task_type=task_type,
            requires_instrument=task_type in {"FFKF_001", "FFYZ_001"},
            requires_human=not is_gate,
            estimated_hours=hours,
            assignee_id=assignee_id if assignee_id is not None else (None if is_gate else 1),
            parent_id=parent_id,
            predecessor_ids=predecessors or [],
            instrument_ids=(instrument_ids if instrument_ids is not None else ([1] if task_type in {"FFKF_001", "FFYZ_001"} else [])),
            is_external_gate=is_gate,
        )


if __name__ == "__main__":
    unittest.main()
