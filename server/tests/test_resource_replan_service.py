import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot
from app.services.resource_replan_service import replan_resource_closure


class ResourceReplanServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_rolls_back_prior_iteration_when_expanded_replan_fails(self):
        project = Project(name="闭包重排项目", code="RESOURCE-ROLLBACK")
        first_task = Task(project=project, name="首轮任务", task_type="test", status="scheduled")
        external_task = Task(project=project, name="外部冲突任务", task_type="test", status="scheduled")
        self.db.add_all([project, first_task, external_task])
        self.db.commit()
        released_at = datetime(2026, 8, 26, 8, 30)
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                self.db.add(TimeSlot(
                    task_id=first_task.id, plan_start=released_at,
                    plan_end=released_at + timedelta(hours=1), status="scheduled",
                ))
                self.db.flush()
                return {"status": "ok", "schedule_run_id": "first-run"}
            return {"status": "error", "message": "扩展闭包后无可行解"}

        with patch(
            "app.services.resource_replan_service.SchedulerService.generate",
            side_effect=generate,
        ), patch(
            "app.services.resource_replan_service.external_conflict_task_ids",
            return_value={external_task.id},
        ):
            result = replan_resource_closure(
                self.db, {first_task.id}, released_at, project.id, max_iterations=2,
            )

        self.assertEqual("error", result["status"])
        self.assertEqual(2, len(calls))
        self.assertEqual([first_task.id], result["replan_diagnostic"]["seed_task_ids"])
        self.assertEqual([first_task.id], result["replan_diagnostic"]["initial_closure_task_ids"])
        self.assertEqual(
            [first_task.id, external_task.id],
            result["replan_diagnostic"]["final_closure_task_ids"],
        )
        self.assertEqual(
            [external_task.id],
            result["replan_diagnostic"]["iterations"][0]["external_conflict_task_ids"],
        )
        self.assertEqual(
            0,
            self.db.query(TimeSlot).filter(TimeSlot.task_id == first_task.id).count(),
        )

    def test_commit_true_commits_only_after_successful_closure(self):
        project = Project(name="提交重排项目", code="RESOURCE-COMMIT")
        task = Task(project=project, name="提交任务", task_type="test", status="scheduled")
        self.db.add_all([project, task])
        self.db.commit()
        start = datetime(2026, 8, 26, 8, 30)

        def generate(**_kwargs):
            self.db.add(TimeSlot(
                task_id=task.id, plan_start=start,
                plan_end=start + timedelta(hours=1), status="scheduled",
            ))
            self.db.flush()
            return {"status": "ok", "schedule_run_id": "commit-run"}

        with patch(
            "app.services.resource_replan_service.SchedulerService.generate",
            side_effect=generate,
        ), patch(
            "app.services.resource_replan_service.external_conflict_task_ids",
            return_value=set(),
        ):
            result = replan_resource_closure(
                self.db, {task.id}, start, project.id, commit=True,
            )

        self.assertEqual("ok", result["status"])
        self.db.expire_all()
        self.assertEqual(1, self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).count())

    def test_bounded_replan_rejects_external_conflict_without_expanding(self):
        project = Project(name="受限重排项目", code="RESOURCE-BOUNDED")
        task = Task(project=project, name="窗口内任务", task_type="test", status="scheduled")
        external = Task(project=project, name="窗口外任务", task_type="test", status="scheduled")
        self.db.add_all([project, task, external])
        self.db.commit()
        start = datetime(2026, 8, 26, 8, 30)

        def generate(**_kwargs):
            self.db.add(TimeSlot(
                task_id=task.id, plan_start=start,
                plan_end=start + timedelta(hours=1), status="scheduled",
            ))
            self.db.flush()
            return {"status": "ok", "schedule_run_id": "bounded-run"}

        with patch(
            "app.services.resource_replan_service.SchedulerService.generate",
            side_effect=generate,
        ), patch(
            "app.services.resource_replan_service.external_conflict_task_ids",
            return_value={external.id},
        ):
            result = replan_resource_closure(
                self.db, {task.id}, start, project.id, expand_closure=False,
            )

        self.assertEqual("error", result["status"])
        self.assertEqual([external.id], result["external_conflict_task_ids"])
        self.assertFalse(result["replan_diagnostic"]["expand_closure"])
        self.assertEqual(
            [external.id],
            result["replan_diagnostic"]["iterations"][0]["external_conflict_task_ids"],
        )
        self.assertEqual(0, self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).count())

    def test_bounded_replan_commits_when_window_has_no_external_conflict(self):
        project = Project(name="受限重排成功项目", code="RESOURCE-BOUNDED-OK")
        task = Task(project=project, name="窗口内任务", task_type="test", status="scheduled")
        self.db.add_all([project, task])
        self.db.commit()
        start = datetime(2026, 8, 26, 8, 30)

        def generate(**_kwargs):
            self.db.add(TimeSlot(
                task_id=task.id, plan_start=start,
                plan_end=start + timedelta(hours=1), status="scheduled",
            ))
            self.db.flush()
            return {"status": "ok", "schedule_run_id": "bounded-ok-run"}

        with patch(
            "app.services.resource_replan_service.SchedulerService.generate",
            side_effect=generate,
        ), patch(
            "app.services.resource_replan_service.external_conflict_task_ids",
            return_value=set(),
        ):
            result = replan_resource_closure(
                self.db, {task.id}, start, project.id, expand_closure=False,
            )

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).count())


if __name__ == "__main__":
    unittest.main()
