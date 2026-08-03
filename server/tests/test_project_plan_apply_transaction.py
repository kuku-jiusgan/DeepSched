import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.schemas.schemas import ProjectPlanApplyResponse
from app.services.project_plan_apply_service import apply_project_plan


class ProjectPlanApplyTransactionTest(unittest.TestCase):
    @patch("app.services.project_plan_apply_service._preview_plan_insert")
    @patch("app.services.project_plan_apply_service._load_insert_movable_tasks")
    @patch("app.services.project_plan_apply_service._selected_tasks_start_today")
    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_project_candidates")
    @patch("app.services.project_plan_apply_service.validate_required_task_instruments")
    @patch("app.services.project_plan_apply_service.validate_project_estimated_hours")
    @patch("app.services.project_plan_apply_service.recalculate_project_parent_hours")
    def test_confirmation_preview_does_not_commit_stable_trial(
        self,
        _recalculate,
        _validate_hours,
        _validate_instruments,
        load_candidates,
        execute_replan,
        starts_today,
        load_movable,
        preview_insert,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1)
        selected = [SimpleNamespace(id=10)]
        movable = [SimpleNamespace(id=20)]
        load_candidates.return_value = (project, selected)
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied",
            project_id=1,
            schedule_run_id="trial-run",
        )
        starts_today.return_value = False
        load_movable.return_value = movable
        preview_insert.return_value = ProjectPlanApplyResponse(
            status="insert_confirmation_required",
            project_id=1,
            preview_token="preview-token",
        )

        result = apply_project_plan(db, 1)

        self.assertEqual("insert_confirmation_required", result.status)
        execute_replan.assert_called_once_with(
            db, project, selected, [], commit=False,
        )
        self.assertFalse(any(
            call.kwargs.get("commit") is True
            for call in execute_replan.call_args_list
        ))

    @patch("app.services.project_plan_apply_service._selected_tasks_start_today")
    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_project_candidates")
    @patch("app.services.project_plan_apply_service.validate_required_task_instruments")
    @patch("app.services.project_plan_apply_service.validate_project_estimated_hours")
    @patch("app.services.project_plan_apply_service.recalculate_project_parent_hours")
    def test_same_day_trial_is_recomputed_and_committed(
        self,
        _recalculate,
        _validate_hours,
        _validate_instruments,
        load_candidates,
        execute_replan,
        starts_today,
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
        starts_today.return_value = True

        result = apply_project_plan(db, 1)

        self.assertEqual("applied", result.status)
        self.assertEqual(
            [False, True],
            [call.kwargs["commit"] for call in execute_replan.call_args_list],
        )
        db.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
