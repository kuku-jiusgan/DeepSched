import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.schedule_early_completion_replan_service import (
    replan_released_resource_queue,
)


class EarlyCompletionReplanServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.instrument = Instrument(code="EC-001", name="提前完成测试仪器")
        self.first_project = Project(code="EC-A", name="前序项目")
        self.next_project = Project(code="EC-B", name="后续项目")
        self.user = User(username="ec-user", display_name="技术员", role="技术员")
        self.db.add_all([self.instrument, self.first_project, self.next_project, self.user])
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def test_replan_preserves_queue_order_and_cross_project_setup(self):
        first = self._task("后续仪器任务一", 2, requires_instrument=True)
        second = self._task("后续仪器任务二", 2, requires_instrument=True)
        self._slot(first, datetime(2026, 8, 26, 10, 0), datetime(2026, 8, 26, 11, 0))
        self._slot(second, datetime(2026, 8, 26, 11, 0), datetime(2026, 8, 26, 12, 0))
        self.db.commit()
        released_at = datetime(2026, 8, 26, 8, 30)

        with patch(
            "app.services.schedule_early_completion_replan_service.load_forward_shift_candidates",
            return_value=[first, second],
        ), patch(
            "app.services.schedule_early_completion_replan_service.is_movable_task",
            return_value=True,
        ), patch(
            "app.services.schedule_early_completion_replan_service.cross_project_setup_minutes",
            return_value=30,
        ), patch(
            "app.services.schedule_early_completion_replan_service.replan_resource_closure",
            return_value={"status": "ok", "schedule_run_id": "run-1"},
        ) as replan:
            result = replan_released_resource_queue(
                self.db, self.instrument.id, released_at, self.user.id, self.first_project.id,
            )

        kwargs = replan.call_args.kwargs
        self.assertEqual({first.id, second.id}, replan.call_args.args[1])
        self.assertEqual({first.id: self.instrument.id, second.id: self.instrument.id}, kwargs["fixed_instrument_ids"])
        self.assertEqual([(second.id, first.id)], kwargs["additional_dependencies"])
        self.assertEqual(released_at + timedelta(minutes=30), kwargs["earliest_start_bounds"][first.id])
        self.assertFalse(kwargs["emit_advance_notifications"])
        self.assertEqual(0, result["moved_tasks"])

    def test_non_instrument_task_is_not_fixed_to_history_slot_instrument(self):
        task = self._task("报告撰写", 1, requires_instrument=False)
        self._slot(task, datetime(2026, 8, 26, 10, 0), datetime(2026, 8, 26, 11, 0))
        self.db.commit()

        with patch(
            "app.services.schedule_early_completion_replan_service.load_forward_shift_candidates",
            return_value=[task],
        ), patch(
            "app.services.schedule_early_completion_replan_service.is_movable_task",
            return_value=True,
        ), patch(
            "app.services.schedule_early_completion_replan_service.replan_resource_closure",
            return_value={"status": "ok", "schedule_run_id": "run-2"},
        ) as replan:
            replan_released_resource_queue(
                self.db, self.instrument.id, datetime(2026, 8, 26, 8, 30), self.user.id,
            )

        self.assertEqual({}, replan.call_args.kwargs["fixed_instrument_ids"])

    def test_first_non_movable_task_stops_candidate_prefix(self):
        movable = self._task("可移动任务", 1, requires_instrument=True)
        frozen = self._task("冻结任务", 1, requires_instrument=True)
        later = self._task("后续任务", 1, requires_instrument=True)
        for index, task in enumerate([movable, frozen, later]):
            start = datetime(2026, 8, 26, 10 + index, 0)
            self._slot(task, start, start + timedelta(hours=1))
        self.db.commit()

        with patch(
            "app.services.schedule_early_completion_replan_service.load_forward_shift_candidates",
            return_value=[movable, frozen, later],
        ), patch(
            "app.services.schedule_early_completion_replan_service.is_movable_task",
            side_effect=[True, False],
        ), patch(
            "app.services.schedule_early_completion_replan_service.replan_resource_closure",
            return_value={"status": "ok", "schedule_run_id": "run-3"},
        ) as replan:
            replan_released_resource_queue(
                self.db, self.instrument.id, datetime(2026, 8, 26, 8, 30), self.user.id,
            )

        self.assertEqual({movable.id}, replan.call_args.args[1])

    def test_failure_keeps_completion_response_shape(self):
        task = self._task("无法重排任务", 1, requires_instrument=True)
        self._slot(task, datetime(2026, 8, 26, 10, 0), datetime(2026, 8, 26, 11, 0))
        self.db.commit()

        with patch(
            "app.services.schedule_early_completion_replan_service.load_forward_shift_candidates",
            return_value=[task],
        ), patch(
            "app.services.schedule_early_completion_replan_service.is_movable_task",
            return_value=True,
        ), patch(
            "app.services.schedule_early_completion_replan_service.replan_resource_closure",
            return_value={"status": "error", "message": "无可行解"},
        ):
            result = replan_released_resource_queue(
                self.db, self.instrument.id, datetime(2026, 8, 26, 8, 30), self.user.id,
            )

        self.assertEqual("error", result["status"])
        self.assertEqual(0, result["moved_tasks"])
        self.assertEqual([], result["moved_task_details"])

    def _task(self, name, project_number, requires_instrument):
        project = self.first_project if project_number == 1 else self.next_project
        task = Task(
            project=project,
            name=name,
            task_type="test",
            status="scheduled",
            requires_instrument=requires_instrument,
            requires_human=True,
            assignee=self.user,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _slot(self, task, start, end):
        self.db.add(TimeSlot(
            task=task,
            instrument_id=self.instrument.id,
            plan_start=start,
            plan_end=end,
            status="scheduled",
        ))


if __name__ == "__main__":
    unittest.main()
