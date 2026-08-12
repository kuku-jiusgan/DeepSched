import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.schemas.schemas import ProjectPlanApplyResponse
from app.services.project_plan_apply_service import apply_project_plan, _preview_plan_insert


class ProjectPlanApplyTransactionTest(unittest.TestCase):
    @patch("app.services.project_plan_apply_service.plan_fingerprint", return_value="token")
    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_insert_movable_tasks")
    def test_preview_without_real_shift_is_committed_without_confirmation(
        self,
        load_movable,
        execute_replan,
        _fingerprint,
    ):
        db = MagicMock()
        project = SimpleNamespace(id=1)
        selected = [SimpleNamespace(id=10)]
        load_movable.return_value = [SimpleNamespace(id=20)]
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied",
            project_id=1,
            schedule_run_id="preview-run",
            moved_tasks=0,
        )

        result = _preview_plan_insert(db, project, selected, "稳定排程失败")

        self.assertEqual("applied", result.status)
        self.assertEqual("排程完成，未顺延其他任务", result.message)
        db.commit.assert_called_once()

    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_project_candidates")
    @patch("app.services.project_plan_apply_service.validate_required_task_instruments")
    @patch("app.services.project_plan_apply_service.validate_project_estimated_hours")
    @patch("app.services.project_plan_apply_service.recalculate_project_parent_hours")
    def test_stable_schedule_is_committed_without_insert_preview(
        self,
        _recalculate,
        _validate_hours,
        _validate_instruments,
        load_candidates,
        execute_replan,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1)
        selected = [SimpleNamespace(id=10)]
        load_candidates.return_value = (project, selected)
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied",
            project_id=1,
            schedule_run_id="trial-run",
        )
        result = apply_project_plan(db, 1)

        self.assertEqual("applied", result.status)
        self.assertEqual(
            [False, True],
            [call.kwargs["commit"] for call in execute_replan.call_args_list],
        )
        db.rollback.assert_called_once()

    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_project_candidates")
    @patch("app.services.project_plan_apply_service.validate_required_task_instruments")
    @patch("app.services.project_plan_apply_service.validate_project_estimated_hours")
    @patch("app.services.project_plan_apply_service.recalculate_project_parent_hours")
    def test_stable_schedule_is_committed_regardless_of_start_day(
        self,
        _recalculate,
        _validate_hours,
        _validate_instruments,
        load_candidates,
        execute_replan,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1)
        selected = [SimpleNamespace(id=10)]
        load_candidates.return_value = (project, selected)
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied",
            project_id=1,
            schedule_run_id="run-1",
        )
        result = apply_project_plan(db, 1)

        self.assertEqual("applied", result.status)
        self.assertEqual(
            [False, True],
            [call.kwargs["commit"] for call in execute_replan.call_args_list],
        )
        db.rollback.assert_called_once()

    @patch("app.services.project_plan_apply_service._load_insert_movable_tasks")
    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_project_candidates")
    @patch("app.services.project_plan_apply_service.validate_required_task_instruments")
    @patch("app.services.project_plan_apply_service.validate_project_estimated_hours")
    @patch("app.services.project_plan_apply_service.recalculate_project_parent_hours")
    def test_approval_context_limits_selected_tasks_to_gate_downstream(
        self,
        _recalculate,
        _validate_hours,
        _validate_instruments,
        load_candidates,
        execute_replan,
        load_movable,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1)
        downstream_task = SimpleNamespace(id=10)
        other_branch_task = SimpleNamespace(id=11)
        context = SimpleNamespace(
            gate_id=3,
            downstream_task_ids={10},
            branch_task_ids={1, 3, 10},
            anchor_at=datetime(2026, 7, 1, 10, 0),
        )
        load_candidates.return_value = (project, [downstream_task, other_branch_task])
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied",
            project_id=1,
            schedule_run_id="trial-run",
        )
        load_movable.return_value = []

        result = apply_project_plan(db, 1, approval_context=context)

        self.assertEqual("applied", result.status)
        self.assertEqual(
            [[10], [10]],
            [
                [task.id for task in call.args[2]]
                for call in execute_replan.call_args_list
            ],
        )
        self.assertTrue(all(
            call.kwargs["approval_context"] is context
            for call in execute_replan.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
