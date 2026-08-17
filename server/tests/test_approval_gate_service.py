import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TaskDependency, TimeSlot, User
from app.schemas.approval_gate_schemas import ApprovalGateCreate, ApprovalGateSubmit
from app.services.approval_gate_service import (
    ApprovalGateInvalidError,
    ApprovalGatePermissionError,
    approve_approval_gate,
    create_approval_gate,
    list_approval_gates,
    submit_approval_gate,
    unapproved_gate_context,
)
from app.services.approval_gate_schedule_context import build_approval_schedule_context


class ApprovalGateServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.manager = User(id=1, username="manager", display_name="项目负责人", role="分析员")
        self.viewer = User(id=2, username="viewer", display_name="其他分析员", role="分析员")
        self.project_admin = User(id=3, username="project-admin", display_name="项目管理员", role="项目管理员")
        self.system_admin = User(id=4, username="system-admin", display_name="系统管理员", role="系统管理员")
        self.project_member = User(id=5, username="member", display_name="项目成员", role="分析员")
        self.project = Project(id=1, name="项目一", code="P-001", manager_id=1, end_date=datetime(2026, 8, 1, 18, 0))
        self.plan = Task(id=1, project_id=1, name="方案编写", task_type="manual", requires_instrument=False, assignee_id=1, status="pending")
        self.validation = Task(id=2, project_id=1, name="方法验证", task_type="instrument", requires_instrument=True, assignee_id=5, status="pending")
        self.db.add_all([
            self.manager, self.viewer, self.project_admin, self.system_admin, self.project_member,
            self.project, self.plan, self.validation,
        ])
        self.db.add(TaskDependency(task_id=2, predecessor_id=1))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_create_gate_connects_predecessor_and_unlock_task(self):
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )

        self.assertTrue(gate.is_external_gate if hasattr(gate, "is_external_gate") else True)
        stored = self.db.query(Task).filter(Task.is_external_gate.is_(True)).one()
        dependencies = self.db.query(TaskDependency).filter(
            (TaskDependency.task_id == stored.id) | (TaskDependency.predecessor_id == stored.id)
        ).all()
        self.assertEqual({1}, {item.predecessor_id for item in dependencies if item.task_id == stored.id})
        self.assertEqual({2}, {item.task_id for item in dependencies if item.predecessor_id == stored.id})
        self.assertEqual("waiting_external", self.db.get(Task, 2).status)
        self.assertEqual(self.manager.id, stored.assignee_id)
        self.assertEqual(self.manager.id, gate.assignee_id)
        self.assertEqual(self.manager.display_name, gate.assignee_name)
        self.assertEqual("方案编写", gate.top_level_task_name)
        self.assertEqual(0, self.db.query(TimeSlot).count())

    def test_create_gate_uses_selected_assignee(self):
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(
                predecessor_task_id=1,
                unlock_task_ids=[2],
                assignee_id=self.project_member.id,
            ),
            self.manager,
        )

        stored = self.db.query(Task).filter(Task.is_external_gate.is_(True)).one()
        self.assertEqual(self.project_member.id, stored.assignee_id)
        self.assertEqual(self.project_member.id, gate.assignee_id)
        self.assertEqual(self.project_member.display_name, gate.assignee_name)

    def test_gate_output_shows_predecessor_top_level_task_name(self):
        top_level_task = Task(
            id=4,
            project_id=1,
            name="LCMS方法开发",
            task_type="manual",
            requires_instrument=False,
            assignee_id=1,
            status="pending",
        )
        self.plan.parent_id = top_level_task.id
        self.db.add(top_level_task)
        self.db.commit()

        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )

        self.assertEqual("LCMS方法开发", gate.top_level_task_name)

    def test_non_manager_cannot_operate_gate(self):
        with self.assertRaises(ApprovalGatePermissionError):
            create_approval_gate(
                self.db,
                1,
                ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
                self.viewer,
            )

    def test_expected_approval_becomes_solver_lower_bound(self):
        gate = Task(
            id=3,
            project_id=1,
            name="方案签批",
            task_type="approval_gate",
            is_external_gate=True,
            gate_status="waiting_approval",
            expected_approval_at=datetime(2026, 7, 20, 9, 0),
            status="waiting_approval",
        )
        self.db.add(gate)
        self.db.add(TaskDependency(task_id=2, predecessor_id=3))
        self.db.commit()

        bounds, forecast_ids = unapproved_gate_context(self.db, [self.validation])

        self.assertEqual(datetime(2026, 7, 20, 9, 0), bounds[2])
        self.assertEqual({2}, forecast_ids)

    def test_schedule_context_uses_current_branch_anchor_only(self):
        gate_out = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        branch_done_at = datetime(2026, 7, 1, 10, 0)
        other_branch_end = datetime(2026, 7, 5, 10, 0)
        other_branch_task = Task(
            id=4,
            project_id=1,
            name="其他顶级任务",
            task_type="manual",
            requires_instrument=False,
            assignee_id=1,
            status="scheduled",
        )
        self.plan.status = "done"
        self.db.add(other_branch_task)
        self.db.add_all([
            TimeSlot(
                schedule_run_id="run-branch",
                task_id=1,
                plan_start=branch_done_at - timedelta(hours=1),
                plan_end=branch_done_at,
                actual_start=branch_done_at - timedelta(hours=1),
                actual_end=branch_done_at,
                status="completed",
            ),
            TimeSlot(
                schedule_run_id="run-other",
                task_id=4,
                plan_start=other_branch_end - timedelta(hours=1),
                plan_end=other_branch_end,
                status="scheduled",
            ),
        ])
        self.db.commit()

        context = build_approval_schedule_context(self.db, self.db.get(Task, gate_out.id))

        self.assertEqual({2}, context.downstream_task_ids)
        self.assertIn(1, context.branch_task_ids)
        self.assertIn(gate_out.id, context.branch_task_ids)
        self.assertNotIn(4, context.branch_task_ids)
        self.assertEqual(branch_done_at, context.anchor_at)

    @patch("app.services.project_plan_apply_service.apply_project_plan")
    def test_submit_records_expected_date(self, apply_project_plan):
        apply_project_plan.return_value = type("Result", (), {
            "status": "applied", "message": "预测排程已更新", "preview_token": None,
            "schedule_run_id": "run-1", "moved_tasks": 0,
        })()
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        self.plan.status = "done"
        self.db.commit()
        result = submit_approval_gate(
            self.db,
            gate.id,
            ApprovalGateSubmit(expected_approval_at=datetime.now() + timedelta(days=3)),
            self.manager,
        )

        self.assertEqual("waiting_approval", result.gate.gate_status)
        self.assertEqual("forecast", result.schedule_status)
        self.assertIsNotNone(result.gate.expected_approval_at)

    @patch("app.services.project_plan_apply_service.apply_project_plan")
    def test_submit_allows_expected_date_before_predecessor_completion(self, apply_project_plan):
        apply_project_plan.return_value = type("Result", (), {
            "status": "applied", "message": "预测排程已更新", "preview_token": None,
            "schedule_run_id": "run-early-forecast", "moved_tasks": 0,
        })()
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )

        result = submit_approval_gate(
            self.db,
            gate.id,
            ApprovalGateSubmit(expected_approval_at=datetime.now() + timedelta(days=3)),
            self.manager,
        )

        self.assertEqual("waiting_approval", result.gate.gate_status)
        apply_project_plan.assert_called_once()

    @patch("app.services.project_plan_apply_service.apply_project_plan")
    def test_submit_allows_expected_time_before_recorded_predecessor_completion(self, apply_project_plan):
        apply_project_plan.return_value = type("Result", (), {
            "status": "applied", "message": "预测排程已更新", "preview_token": None,
            "schedule_run_id": "run-before-completion", "moved_tasks": 0,
        })()
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        completed_at = datetime.now() + timedelta(days=2)
        self.plan.status = "done"
        self.db.add(TimeSlot(
            schedule_run_id="run-completed",
            task_id=self.plan.id,
            plan_start=completed_at - timedelta(hours=1),
            plan_end=completed_at,
            actual_start=completed_at - timedelta(hours=1),
            actual_end=completed_at,
            status="completed",
        ))
        self.db.commit()

        result = submit_approval_gate(
            self.db,
            gate.id,
            ApprovalGateSubmit(expected_approval_at=datetime.now() + timedelta(days=1)),
            self.manager,
        )

        self.assertEqual("waiting_approval", result.gate.gate_status)
        apply_project_plan.assert_called_once()

    def test_workspace_gate_exposes_predecessor_status_and_completion_time(self):
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        completed_at = datetime.now()
        self.plan.status = "done"
        self.db.add(TimeSlot(
            schedule_run_id="run-workspace",
            task_id=self.plan.id,
            plan_start=completed_at - timedelta(hours=1),
            plan_end=completed_at,
            actual_start=completed_at - timedelta(hours=1),
            actual_end=completed_at,
            status="completed",
        ))
        self.db.commit()

        result = list_approval_gates(self.db, self.manager, workspace_only=True)

        predecessor = result.items[0].predecessor_tasks[0]
        self.assertEqual("completed", predecessor.status)
        self.assertEqual(completed_at, predecessor.completed_at)

    @patch("app.services.project_plan_apply_service.apply_project_plan")
    def test_approve_rejects_incomplete_predecessor(self, apply_project_plan):
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )

        with self.assertRaisesRegex(ApprovalGateInvalidError, "方案编写.*尚未完成"):
            approve_approval_gate(self.db, gate.id, None, self.manager)

        apply_project_plan.assert_not_called()

    def test_list_pending_and_approved_counts(self):
        create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )

        result = list_approval_gates(self.db, self.manager, status="pending")

        self.assertEqual(1, result.total)
        self.assertEqual(1, result.pending_count)
        self.assertEqual(0, result.approved_count)

    def test_workspace_cards_include_project_members_but_project_admin_read_only(self):
        gate_out = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        gate = self.db.get(Task, gate_out.id)
        gate.assignee_id = self.viewer.id
        self.db.commit()

        manager_result = list_approval_gates(self.db, self.manager, workspace_only=True)
        assignee_result = list_approval_gates(self.db, self.viewer, workspace_only=True)
        member_result = list_approval_gates(self.db, self.project_member, workspace_only=True)
        project_admin_result = list_approval_gates(self.db, self.project_admin, workspace_only=True)
        system_admin_result = list_approval_gates(self.db, self.system_admin, workspace_only=True)

        self.assertEqual(1, manager_result.total)
        self.assertTrue(manager_result.items[0].can_operate)
        self.assertEqual(1, assignee_result.total)
        self.assertTrue(assignee_result.items[0].can_operate)
        self.assertEqual(self.viewer.id, assignee_result.items[0].assignee_id)
        self.assertEqual(1, member_result.total)
        self.assertTrue(member_result.items[0].can_operate)
        self.assertEqual(1, project_admin_result.total)
        self.assertFalse(project_admin_result.items[0].can_operate)
        self.assertEqual(1, system_admin_result.total)

    @patch("app.services.project_plan_apply_service.apply_project_plan")
    def test_project_member_can_approve_gate(self, apply_project_plan):
        apply_project_plan.return_value = type("Result", (), {
            "status": "applied", "message": "正式排程已更新", "preview_token": None,
            "schedule_run_id": "run-member-approve", "moved_tasks": 0,
        })()
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        self.plan.status = "done"
        self.db.commit()

        result = approve_approval_gate(self.db, gate.id, None, self.project_member)

        self.assertEqual("approved", result.gate.gate_status)
        self.assertEqual(self.project_member.display_name, result.gate.approved_by_name)

    @patch("app.services.project_plan_apply_service.apply_project_plan")
    def test_approved_gate_uses_actual_time_and_is_not_forecast(self, apply_project_plan):
        apply_project_plan.return_value = type("Result", (), {
            "status": "applied", "message": "正式排程已更新", "preview_token": None,
            "schedule_run_id": "run-2", "moved_tasks": 0,
        })()
        gate_out = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        gate = self.db.get(Task, gate_out.id)
        gate.gate_status = "waiting_approval"
        gate.status = "waiting_approval"
        gate.expected_approval_at = datetime.now() + timedelta(days=3)
        self.plan.status = "done"
        self.db.commit()

        result = approve_approval_gate(self.db, gate.id, None, self.manager)
        refreshed_validation = self.db.get(Task, 2)
        bounds, forecast_ids = unapproved_gate_context(self.db, [refreshed_validation])

        self.assertEqual("approved", result.gate.gate_status)
        self.assertEqual(result.gate.approved_at, bounds[2])
        self.assertEqual(set(), forecast_ids)

    @patch("app.services.project_plan_apply_service.apply_project_plan")
    def test_not_submitted_gate_can_be_confirmed_complete_directly(self, apply_project_plan):
        apply_project_plan.return_value = type("Result", (), {
            "status": "applied", "message": "正式排程已更新", "preview_token": None,
            "schedule_run_id": "run-direct", "moved_tasks": 0,
        })()
        gate = create_approval_gate(
            self.db,
            1,
            ApprovalGateCreate(predecessor_task_id=1, unlock_task_ids=[2]),
            self.manager,
        )
        self.plan.status = "done"
        self.db.commit()

        result = approve_approval_gate(self.db, gate.id, None, self.manager)

        self.assertEqual("approved", result.gate.gate_status)
        self.assertIsNotNone(result.gate.submitted_at)
        self.assertEqual(result.gate.submitted_at, result.gate.approved_at)


if __name__ == "__main__":
    unittest.main()
