import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot, User
from app.services.instrument_fault_schedule_service import shift_faulted_instrument_slots


def working_options(_db, start: datetime) -> dict:
    return {
        "day_start_minutes": 0,
        "day_end_minutes": 24 * 60,
        "include_weekends": True,
        "include_holidays": True,
        "horizon_end": start + timedelta(days=30),
        "calendar_days": {},
    }


class InstrumentFaultScheduleServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_fault_shifts_pending_slots_on_faulted_instrument(self):
        project = Project(
            name="故障顺延项目",
            code="FAULT-PENDING",
            end_date=datetime(2026, 8, 31, 23, 59, 59),
        )
        instrument = Instrument(code="ZBYY-002-0001", name="三重四极液质联用仪")
        task = Task(
            project=project,
            name="方法开发",
            task_type="test",
            status="paused",
            requires_instrument=True,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=task.id,
                instrument_id=instrument.id,
                plan_start=datetime(2026, 8, 10, 8, 30),
                plan_end=datetime(2026, 8, 10, 20, 0),
                status="pending",
                tier="confirmed",
            ),
            TimeSlot(
                task_id=task.id,
                instrument_id=instrument.id,
                plan_start=datetime(2026, 8, 11, 8, 30),
                plan_end=datetime(2026, 8, 11, 20, 0),
                status="pending",
                tier="confirmed",
            ),
        ])
        self.db.commit()

        impact = self._shift(instrument, datetime(2026, 8, 10, 9, 32), datetime(2026, 8, 12, 9, 32))

        shifted_slots = self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).order_by(TimeSlot.plan_start).all()
        self.assertEqual(1, len(shifted_slots))
        self.assertEqual(datetime(2026, 8, 12, 10, 0), shifted_slots[0].plan_start)
        self.assertEqual("pending", shifted_slots[0].status)
        self.assertEqual(2, impact["shifted_slots"])
        self.assertEqual(1, impact["affected_tasks"])
        self.assertEqual(0, impact["risk_tasks"])

    def test_fault_cascades_to_dependencies_but_not_manual_same_owner_task(self):
        assignee = User(
            username="analyst",
            display_name="分析员",
            role="技术员",
            is_active=True,
        )
        project = Project(
            name="级联顺延项目",
            code="FAULT-CASCADE",
            end_date=datetime(2026, 8, 31, 23, 59, 59),
        )
        faulted_instrument = Instrument(code="ZBYY-002-0001", name="故障仪器")
        other_instrument = Instrument(code="ZBYY-002-0002", name="关联仪器")
        root_task = Task(
            project=project,
            name="故障仪器任务",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
            requires_human=True,
            assignee=assignee,
        )
        dependency_task = Task(
            project=project,
            name="后继依赖任务",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
        )
        same_owner_task = Task(
            project=project,
            name="同负责人方案撰写",
            task_type="test",
            status="scheduled",
            requires_instrument=False,
            requires_human=True,
            assignee=assignee,
        )
        self.db.add_all([
            assignee,
            project,
            faulted_instrument,
            other_instrument,
            root_task,
            dependency_task,
            same_owner_task,
        ])
        self.db.flush()
        self.db.add(TaskDependency(task_id=dependency_task.id, predecessor_id=root_task.id))
        self.db.add_all([
            TimeSlot(
                task_id=root_task.id,
                instrument_id=faulted_instrument.id,
                plan_start=datetime(2026, 8, 10, 8, 30),
                plan_end=datetime(2026, 8, 10, 9, 30),
                status="scheduled",
            ),
            TimeSlot(
                task_id=same_owner_task.id,
                instrument_id=None,
                plan_start=datetime(2026, 8, 12, 9, 0),
                plan_end=datetime(2026, 8, 12, 10, 0),
                status="scheduled",
            ),
            TimeSlot(
                task_id=dependency_task.id,
                instrument_id=other_instrument.id,
                plan_start=datetime(2026, 8, 10, 12, 0),
                plan_end=datetime(2026, 8, 10, 13, 0),
                status="scheduled",
            ),
        ])
        self.db.commit()

        impact = self._shift(faulted_instrument, datetime(2026, 8, 10, 8, 45), datetime(2026, 8, 12, 8, 45))

        root_slot = self._only_slot(root_task.id)
        same_owner_slot = self._only_slot(same_owner_task.id)
        dependency_slot = self._only_slot(dependency_task.id)
        self.assertGreaterEqual(root_slot.plan_start, datetime(2026, 8, 12, 8, 45))
        self.assertEqual(datetime(2026, 8, 12, 9, 0), same_owner_slot.plan_start)
        self.assertLess(same_owner_slot.plan_start, root_slot.plan_end)
        self.assertGreaterEqual(dependency_slot.plan_start, root_slot.plan_end)
        self.assertEqual(2, impact["affected_tasks"])

    def test_fault_allows_shift_past_project_end_and_marks_risk(self):
        project = Project(
            name="截止项目",
            code="FAULT-RISK",
            end_date=datetime(2026, 8, 12, 9, 0),
        )
        instrument = Instrument(code="ZBYY-002-0001", name="故障仪器")
        task = Task(
            project=project,
            name="超期任务",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id,
            instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 10, 8, 30),
            plan_end=datetime(2026, 8, 10, 10, 30),
            status="scheduled",
        ))
        self.db.commit()

        impact = self._shift(instrument, datetime(2026, 8, 10, 8, 45), datetime(2026, 8, 12, 8, 45))

        shifted = self._only_slot(task.id)
        self.assertGreater(shifted.plan_end, project.end_date)
        self.assertEqual(1, impact["risk_tasks"])
        self.assertFalse(impact["affected_task_details"][0]["can_shift"])
        self.assertIn("超期风险", impact["affected_task_details"][0]["reason"])

    def _shift(self, instrument: Instrument, reported_at: datetime, estimated_resolved_at: datetime) -> dict:
        with patch(
            "app.services.instrument_fault_schedule_service._load_working_options",
            side_effect=working_options,
        ), patch(
            "app.services.instrument_fault_notification_service.push_by_rule",
            return_value=0,
        ), patch(
            "app.services.instrument_fault_schedule_service.notify_rescheduled_tasks_delayed",
        ):
            return shift_faulted_instrument_slots(
                self.db,
                instrument,
                reported_at,
                estimated_resolved_at,
            )

    def _only_slot(self, task_id: int) -> TimeSlot:
        return self.db.query(TimeSlot).filter(TimeSlot.task_id == task_id).one()


if __name__ == "__main__":
    unittest.main()
