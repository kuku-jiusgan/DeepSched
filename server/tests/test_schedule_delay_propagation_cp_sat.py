import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot, User
from app.services.schedule_delay_propagation_service import propagate_actual_delay


class ScheduleDelayPropagationCpSatTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.project = Project(code="DELAY-CP", name="延期重排项目")
        self.user = User(username="delay-user", display_name="技术员", role="技术员")
        self.db.add_all([self.project, self.user])
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def test_uses_cp_sat_for_complete_future_queue(self):
        completed = Task(project=self.project, name="已完成任务", task_type="test", status="completed")
        following = Task(
            project=self.project, name="后续任务", task_type="test", status="scheduled",
            requires_human=True, assignee=self.user,
        )
        self.db.add_all([completed, following])
        self.db.flush()
        slot = TimeSlot(
            task=following, plan_start=datetime(2026, 8, 26, 10, 0),
            plan_end=datetime(2026, 8, 26, 11, 0), status="scheduled",
        )
        self.db.add(slot)
        self.db.commit()

        with patch(
            "app.services.schedule_delay_propagation_service.replan_resource_closure",
            return_value={"status": "ok", "schedule_run_id": "delay-run"},
        ) as replan, patch(
            "app.services.schedule_delay_propagation_service.notify_rescheduled_tasks_delayed",
        ) as notify:
            result = propagate_actual_delay(
                self.db, completed, datetime(2026, 8, 26, 9, 0),
                datetime(2026, 8, 26, 9, 30),
            )

        kwargs = replan.call_args.kwargs
        self.assertEqual({following.id}, replan.call_args.args[1])
        self.assertEqual({following.id: datetime(2026, 8, 26, 9, 30)}, kwargs["earliest_start_bounds"])
        self.assertFalse(kwargs["emit_advance_notifications"])
        self.assertEqual(1, result["affected_tasks"])
        notify.assert_called_once()

    def test_uses_legacy_path_for_unassigned_human_history(self):
        completed = Task(project=self.project, name="已完成任务", task_type="test", status="completed")
        following = Task(
            project=self.project, name="历史任务", task_type="test", status="scheduled",
            requires_human=True, assignee_id=None,
        )
        self.db.add_all([completed, following])
        self.db.flush()
        self.db.add(TimeSlot(
            task=following, plan_start=datetime(2026, 8, 26, 10, 0),
            plan_end=datetime(2026, 8, 26, 11, 0), status="scheduled",
        ))
        self.db.commit()

        with patch(
            "app.services.schedule_delay_propagation_service.replan_resource_closure",
        ) as replan, patch(
            "app.services.schedule_delay_propagation_service._restore_shifted_task",
        ) as restore:
            propagate_actual_delay(
                self.db, completed, datetime(2026, 8, 26, 9, 0),
                datetime(2026, 8, 26, 9, 30),
            )

        replan.assert_not_called()
        restore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
