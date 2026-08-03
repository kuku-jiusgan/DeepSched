import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot, User
from app.services.schedule_delay_service import (
    ScheduleDelayInvalidError,
    report_task_delay as report_task_delay_service,
)
from app.api.transactions import execute_transaction
from app.api.schedules import delay_task
from app.schemas.schemas import TaskDelayRequest


def report_task_delay(db, slot_id, delay_hours, reason):
    return execute_transaction(
        db,
        lambda: report_task_delay_service(db, slot_id, delay_hours, reason),
    )


class ScheduleDelayTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_delay_route_passes_operator_name_to_service(self):
        request = TaskDelayRequest(delay_hours=2, reason="实验延迟")
        user = SimpleNamespace(display_name="王福芳", username="wangfufang")
        expected = {
            "status": "ok", "task_id": 17, "slot_id": 5,
            "delay_hours": 2, "shifted_slots": 1,
            "affected_tasks": 2, "reason": "实验延迟",
        }

        with patch(
            "app.api.schedules.report_task_delay",
            return_value=expected,
        ) as report:
            result = delay_task(5, request, self.db, user)

        self.assertEqual(expected, result)
        report.assert_called_once_with(self.db, 5, 2, "实验延迟", "王福芳")

    def test_delay_from_current_card_extends_final_task_slot(self):
        task = Task(project_id=1, name="multi-day", task_type="test", status="scheduled")
        self.db.add(task)
        self.db.flush()
        current_slot = TimeSlot(
            task_id=task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 13, 8, 30),
            plan_end=datetime(2026, 7, 13, 20, 0), status="scheduled",
        )
        final_slot = TimeSlot(
            task_id=task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 14, 8, 30),
            plan_end=datetime(2026, 7, 14, 18, 30), status="scheduled",
        )
        self.db.add_all([current_slot, final_slot])
        self.db.commit()

        result = report_task_delay(self.db, current_slot.id, 2, "实验延迟")

        self.db.refresh(current_slot)
        self.db.refresh(final_slot)
        task_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == task.id,
        ).order_by(TimeSlot.plan_start).all()
        self.assertEqual(datetime(2026, 7, 13, 20, 0), current_slot.plan_end)
        self.assertEqual(datetime(2026, 7, 14, 20, 0), final_slot.plan_end)
        self.assertEqual(datetime(2026, 7, 15, 8, 30), task_slots[-1].plan_start)
        self.assertEqual(datetime(2026, 7, 15, 9, 0), task_slots[-1].plan_end)
        self.assertEqual(final_slot.id, result["slot_id"])
        self.db.refresh(task)
        self.assertEqual("delayed", task.delay_status)

    def test_following_task_delay_respects_working_hours(self):
        delayed_task = Task(project_id=1, name="delayed", task_type="test", status="scheduled")
        following_task = Task(project_id=1, name="following", task_type="test", status="scheduled")
        self.db.add_all([delayed_task, following_task])
        self.db.flush()
        delayed_slot = TimeSlot(
            task_id=delayed_task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 13, 8, 30),
            plan_end=datetime(2026, 7, 13, 18, 30), status="scheduled",
        )
        following_slot = TimeSlot(
            task_id=following_task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 13, 18, 30),
            plan_end=datetime(2026, 7, 13, 20, 0), status="scheduled",
        )
        self.db.add_all([delayed_slot, following_slot])
        self.db.commit()

        report_task_delay(self.db, delayed_slot.id, 2, "实验延迟")

        shifted_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following_task.id,
            TimeSlot.status == "scheduled",
        ).order_by(TimeSlot.plan_start).all()
        self.assertEqual(1, len(shifted_slots))
        self.assertEqual(datetime(2026, 7, 14, 9, 0), shifted_slots[0].plan_start)
        self.assertEqual(datetime(2026, 7, 14, 10, 30), shifted_slots[0].plan_end)

    def test_delay_shifts_same_assignee_task_in_another_project(self):
        assignee = User(
            username="shared-owner", display_name="共同负责人",
            role="分析员", is_active=True,
        )
        delayed_task = Task(
            project_id=1, name="delayed", task_type="test", status="scheduled",
            requires_human=True, assignee=assignee,
        )
        following_task = Task(
            project_id=2, name="following", task_type="test", status="scheduled",
            requires_human=True, assignee=assignee,
        )
        self.db.add_all([assignee, delayed_task, following_task])
        self.db.flush()
        delayed_slot = TimeSlot(
            task_id=delayed_task.id,
            plan_start=datetime(2026, 7, 13, 8, 30),
            plan_end=datetime(2026, 7, 13, 9, 0), status="scheduled",
        )
        following_slot = TimeSlot(
            task_id=following_task.id,
            plan_start=datetime(2026, 7, 13, 9, 0),
            plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
        )
        self.db.add_all([delayed_slot, following_slot])
        self.db.commit()

        report_task_delay(self.db, delayed_slot.id, 1.5, "实验延迟")

        shifted = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following_task.id,
        ).one()
        self.assertEqual(datetime(2026, 7, 13, 10, 30), shifted.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 18, 30), shifted.plan_end)

    def test_delay_rejects_delayed_task_past_project_end(self):
        project = Project(
            name="截止项目",
            code="DELAY-END-1",
            end_date=datetime(2026, 7, 14, 23, 59, 59),
        )
        task = Task(
            project=project,
            name="方法开发",
            task_type="test",
            status="scheduled",
        )
        self.db.add_all([project, task])
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id,
            instrument_id=1,
            plan_start=datetime(2026, 7, 14, 18, 0),
            plan_end=datetime(2026, 7, 14, 20, 0),
            status="scheduled",
        )
        self.db.add(slot)
        self.db.commit()

        with self.assertRaisesRegex(
            ScheduleDelayInvalidError,
            "项目【截止项目】任务【方法开发】.*超过项目结题时间",
        ):
            report_task_delay(self.db, slot.id, 2, "实验延迟")

        self.db.refresh(task)
        self.db.refresh(slot)
        self.assertEqual("scheduled", task.status)
        self.assertEqual("not_delayed", task.delay_status)
        self.assertEqual(datetime(2026, 7, 14, 20, 0), slot.plan_end)

    def test_delay_rejects_following_task_past_its_project_end(self):
        delayed_project = Project(
            name="延期项目",
            code="DELAY-END-2",
            end_date=datetime(2026, 7, 20, 23, 59, 59),
        )
        following_project = Project(
            name="被影响项目",
            code="DELAY-END-3",
            end_date=datetime(2026, 7, 15, 23, 59, 59),
        )
        delayed_task = Task(
            project=delayed_project,
            name="延期任务",
            task_type="test",
            status="scheduled",
        )
        following_task = Task(
            project=following_project,
            name="后续任务",
            task_type="test",
            status="scheduled",
        )
        self.db.add_all([
            delayed_project,
            following_project,
            delayed_task,
            following_task,
        ])
        self.db.flush()
        delayed_slot = TimeSlot(
            task_id=delayed_task.id,
            instrument_id=1,
            plan_start=datetime(2026, 7, 14, 18, 0),
            plan_end=datetime(2026, 7, 14, 20, 0),
            status="scheduled",
        )
        following_slot = TimeSlot(
            task_id=following_task.id,
            instrument_id=1,
            plan_start=datetime(2026, 7, 15, 18, 30),
            plan_end=datetime(2026, 7, 15, 20, 0),
            status="scheduled",
        )
        self.db.add_all([delayed_slot, following_slot])
        self.db.commit()

        with self.assertRaisesRegex(
            ScheduleDelayInvalidError,
            "项目【被影响项目】任务【后续任务】.*超过项目结题时间",
        ):
            report_task_delay(self.db, delayed_slot.id, 2, "实验延迟")

        self.db.refresh(following_slot)
        self.assertEqual(datetime(2026, 7, 15, 18, 30), following_slot.plan_start)
        self.assertEqual(datetime(2026, 7, 15, 20, 0), following_slot.plan_end)


if __name__ == "__main__":
    unittest.main()
