import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.schemas.schemas import ProjectPlanApplyResponse
from app.services.project_plan_apply_service import (
    _execute_replan,
    _has_approved_gate_predecessor,
    _preview_plan_insert,
    apply_project_plan,
)
from app.services.project_plan_apply_helpers import expand_movable_downstream_tasks
from app.models import Project, Task, TaskDependency
from app.core.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class ProjectPlanApplyTransactionTest(unittest.TestCase):
    def test_moved_predecessor_includes_unstarted_scheduled_successor(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(code="B", name="测试项目B", priority=3)
            db.add(project)
            db.flush()
            development = Task(
                project_id=project.id, name="方法开发", task_type="test",
                status="scheduled",
            )
            writing = Task(
                project_id=project.id, name="方案撰写", task_type="manual",
                status="scheduled",
            )
            db.add_all([development, writing])
            db.flush()
            db.add(TaskDependency(
                task_id=writing.id,
                predecessor_id=development.id,
            ))
            db.commit()

            result = expand_movable_downstream_tasks(db, [development])

            self.assertEqual(
                [development.id, writing.id],
                [task.id for task in result],
            )
        finally:
            db.close()

    def test_replan_keeps_paused_task_status(self):
        """顺延暂停中的任务可以，把它的执行状态一并改掉不行。

        暂停任务本来就允许被顺延（候选筛选放行了 paused），但重排前一路重置成
        pending、求解后落成 scheduled，别人项目的一次保存并排程就把这个任务的
        暂停状态和暂停原因抹掉了。
        """
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(code="P-PAUSE", name="暂停保护项目", priority=3)
            db.add(project)
            db.flush()
            paused = Task(
                project_id=project.id, name="方法开发", task_type="test",
                status="paused", est_duration_hours=4,
            )
            waiting = Task(
                project_id=project.id, name="方案撰写", task_type="manual",
                status="scheduled", est_duration_hours=1,
            )
            db.add_all([paused, waiting])
            db.commit()

            solver = MagicMock()
            solver.return_value.generate.return_value = {"status": "ok", "schedule_run_id": "run-1"}
            with patch("app.services.scheduler.SchedulerService", solver), \
                 patch("app.services.project_plan_apply_service._project_completions", return_value={}), \
                 patch("app.services.project_plan_apply_service._pending_approval_workload", return_value={}), \
                 patch("app.services.project_plan_apply_service.recalculate_project_parent_hours"), \
                 patch("app.services.project_plan_apply_service.validate_project_estimated_hours"):
                _execute_replan(
                    db, project, [waiting], [paused], commit=False,
                )

            db.refresh(paused)
            db.refresh(waiting)
            self.assertEqual("paused", paused.status)
            self.assertEqual("pending", waiting.status)
            preserved = solver.return_value.generate.call_args.kwargs[
                "preserved_status_task_ids"
            ]
            self.assertEqual({paused.id}, preserved)
        finally:
            db.close()

    def test_formally_approved_branch_is_protected_from_forecast_insert(self):
        approved_gate = SimpleNamespace(
            id=1,
            is_external_gate=True,
            gate_status="approved",
            predecessors=[],
        )
        dependency = SimpleNamespace(predecessor=approved_gate)
        validation = SimpleNamespace(id=2, predecessors=[dependency])

        self.assertTrue(_has_approved_gate_predecessor(validation))

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

        result = _preview_plan_insert(db, project, selected)

        self.assertEqual("applied", result.status)
        self.assertEqual("排程完成，未顺延其他任务", result.message)
        db.commit.assert_called_once()

    @patch("app.services.project_plan_apply_service._load_insert_movable_tasks", return_value=[])
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
        _load_movable,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1, project_kind="project")
        selected = [SimpleNamespace(id=10)]
        load_candidates.return_value = (project, selected)
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied",
            project_id=1,
            schedule_run_id="trial-run",
        )
        result = apply_project_plan(db, 1)

        self.assertEqual("applied", result.status)
        self.assertEqual([True], [call.kwargs["commit"] for call in execute_replan.call_args_list])
        db.rollback.assert_not_called()

    @patch("app.services.project_plan_apply_service._load_insert_movable_tasks", return_value=[])
    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_project_candidates")
    @patch("app.services.project_plan_apply_service.validate_required_task_instruments")
    @patch("app.services.project_plan_apply_service.validate_project_estimated_hours")
    @patch("app.services.project_plan_apply_service.recalculate_project_parent_hours")
    def test_save_and_schedule_keeps_uncommitted_drafts_when_trial_replans(
        self,
        _recalculate,
        _validate_hours,
        _validate_instruments,
        load_candidates,
        execute_replan,
        _load_movable,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1, project_kind="project")
        selected = [SimpleNamespace(id=10)]
        load_candidates.return_value = (project, selected)
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied", project_id=1, schedule_run_id="run-1",
        )

        result = apply_project_plan(db, 1, preserve_existing=True)

        self.assertEqual("applied", result.status)
        self.assertEqual([False], [
            call.kwargs["commit"] for call in execute_replan.call_args_list
        ])
        self.assertEqual([True], [
            call.kwargs["use_savepoint"] for call in execute_replan.call_args_list
        ])
        db.rollback.assert_not_called()

    @patch("app.services.project_plan_apply_service.plan_fingerprint", return_value="token")
    @patch("app.services.project_plan_apply_service._load_insert_movable_tasks")
    @patch("app.services.project_plan_apply_service._execute_replan")
    @patch("app.services.project_plan_apply_service._load_project_candidates")
    @patch("app.services.project_plan_apply_service.validate_required_task_instruments")
    @patch("app.services.project_plan_apply_service.validate_project_estimated_hours")
    @patch("app.services.project_plan_apply_service.recalculate_project_parent_hours")
    def test_detection_schedule_requires_confirmation_when_other_projects_move(
        self,
        _recalculate,
        _validate_hours,
        _validate_instruments,
        load_candidates,
        execute_replan,
        load_movable,
        _fingerprint,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1, project_kind="detection")
        selected = [SimpleNamespace(id=10)]
        movable = [SimpleNamespace(id=20)]
        load_candidates.return_value = (project, selected)
        load_movable.return_value = movable
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied", project_id=1, schedule_run_id="priority-run", moved_tasks=1,
        )

        result = apply_project_plan(db, 1)

        # 检测插单顺延了其它项目时，必须先把影响交给用户确认，不再直接落地。
        self.assertEqual("insert_confirmation_required", result.status)
        self.assertIsNotNone(result.preview_token)
        self.assertEqual([False], [call.kwargs["commit"] for call in execute_replan.call_args_list])
        self.assertEqual(movable, execute_replan.call_args_list[0].args[3])

    @patch("app.services.project_plan_apply_service._load_insert_movable_tasks", return_value=[])
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
        _load_movable,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        project = SimpleNamespace(id=1, project_kind="project")
        selected = [SimpleNamespace(id=10)]
        load_candidates.return_value = (project, selected)
        execute_replan.return_value = ProjectPlanApplyResponse(
            status="applied",
            project_id=1,
            schedule_run_id="run-1",
        )
        result = apply_project_plan(db, 1)

        self.assertEqual("applied", result.status)
        self.assertEqual([True], [call.kwargs["commit"] for call in execute_replan.call_args_list])
        db.rollback.assert_not_called()

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
        project = SimpleNamespace(id=1, project_kind="project")
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
            [[10]],
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
