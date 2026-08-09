import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskExecutionSegment, TimeSlot, User
from app.services.workspace_service import (
    WorkspaceAgendaInvalidError,
    WorkspaceAgendaPermissionError,
    get_workspace_agenda,
)


class WorkspaceAgendaServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.technician = User(
            username="tech",
            display_name="技术员",
            role="技术员",
            roles=["技术员"],
            is_active=True,
        )
        self.manager = User(
            username="leader",
            display_name="组长",
            role="技术组长",
            roles=["技术组长"],
            is_active=True,
        )
        self.project = Project(code="XM-001", name="测试项目")
        self.instrument = Instrument(code="LCMS-01", name="液质联用仪")
        self.db.add_all([self.technician, self.manager, self.project, self.instrument])
        self.db.flush()
        parent = Task(
            project_id=self.project.id,
            name="LCMS",
            task_type="任务组",
            requires_human=False,
        )
        self.db.add(parent)
        self.db.flush()
        task = Task(
            project_id=self.project.id,
            name="方法开发",
            task_type="FFKF_001",
            assignee_id=self.technician.id,
            parent_id=parent.id,
            requires_instrument=True,
            status="scheduled",
        )
        self.db.add(task)
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 10, 8, 30),
            plan_end=datetime(2026, 8, 10, 12, 30),
            status="scheduled",
            tier="confirmed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_returns_overlapping_slots_with_task_context(self):
        result = get_workspace_agenda(
            self.db,
            self.technician,
            date(2026, 8, 10),
            date(2026, 8, 16),
        )

        self.assertFalse(result.can_select_assignee)
        self.assertEqual("技术员", result.assignee.display_name)
        self.assertEqual(1, len(result.items))
        self.assertEqual("LCMS", result.items[0].top_level_task_name)
        self.assertEqual("LCMS-01", result.items[0].instrument_code)

    def test_returns_unfinished_overdue_slot_when_today_is_in_range(self):
        task = self.db.query(Task).filter(Task.name == "方法开发").one()
        slot = self.db.query(TimeSlot).one()
        task.status = "running"
        task.delay_status = "delayed"
        slot.status = "running"
        slot.plan_start = datetime(2026, 8, 5, 19, 30)
        slot.plan_end = datetime(2026, 8, 6, 16)
        slot.actual_start = datetime(2026, 8, 5, 14, 3)
        self.db.commit()

        result = get_workspace_agenda(
            self.db,
            self.technician,
            date(2026, 8, 7),
            date(2026, 8, 13),
            today=date(2026, 8, 7),
        )

        self.assertEqual(1, len(result.items))
        self.assertEqual(slot.id, result.items[0].slot_id)
        self.assertEqual(datetime(2026, 8, 5, 14, 3), result.items[0].actual_start)
        self.assertIsNone(result.items[0].actual_end)

    def test_does_not_return_overdue_slot_when_today_is_outside_range(self):
        task = self.db.query(Task).filter(Task.name == "方法开发").one()
        slot = self.db.query(TimeSlot).one()
        task.status = "running"
        task.delay_status = "delayed"
        slot.status = "running"
        slot.plan_start = datetime(2026, 8, 5, 19, 30)
        slot.plan_end = datetime(2026, 8, 6, 16)
        slot.actual_start = datetime(2026, 8, 5, 14, 3)
        self.db.commit()

        result = get_workspace_agenda(
            self.db,
            self.technician,
            date(2026, 8, 8),
            date(2026, 8, 14),
            today=date(2026, 8, 7),
        )

        self.assertEqual([], result.items)

    def test_exposes_task_plan_end_beyond_requested_range(self):
        task = self.db.query(Task).filter(Task.name == "方法开发").one()
        slot = self.db.query(TimeSlot).one()
        task.status = "paused"
        slot.status = "paused"
        slot.plan_start = datetime(2026, 8, 5, 19, 30)
        slot.plan_end = datetime(2026, 8, 6, 16)
        slot.actual_start = datetime(2026, 8, 5, 14, 3)
        slot.actual_end = datetime(2026, 8, 7, 9)
        self.db.add(TimeSlot(
            task_id=task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 20, 8, 30),
            plan_end=datetime(2026, 8, 20, 12, 30),
            status="paused",
            tier="confirmed",
        ))
        self.db.commit()

        result = get_workspace_agenda(
            self.db,
            self.technician,
            date(2026, 8, 7),
            date(2026, 8, 13),
            today=date(2026, 8, 7),
        )

        self.assertEqual(1, len(result.items))
        self.assertEqual(datetime(2026, 8, 20, 12, 30), result.items[0].task_plan_end)

    def test_keeps_paused_slot_and_started_replacement_on_today(self):
        source_task = self.db.query(Task).filter(Task.name == "方法开发").one()
        source_slot = self.db.query(TimeSlot).one()
        source_task.status = "paused"
        source_slot.status = "paused"
        source_slot.plan_start = datetime(2026, 8, 5, 19, 30)
        source_slot.plan_end = datetime(2026, 8, 6, 16)
        source_slot.actual_start = datetime(2026, 8, 5, 14, 3)
        source_slot.actual_end = datetime(2026, 8, 7, 9)

        replacement_task = Task(
            project_id=self.project.id,
            name="接替任务",
            task_type="FFKF_002",
            assignee_id=self.technician.id,
            requires_instrument=True,
            status="running",
        )
        self.db.add(replacement_task)
        self.db.flush()
        replacement_slot = TimeSlot(
            task_id=replacement_task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 20, 8, 30),
            plan_end=datetime(2026, 8, 20, 12),
            actual_start=datetime(2026, 8, 7, 9),
            status="running",
            tier="confirmed",
        )
        self.db.add(replacement_slot)
        self.db.commit()

        result = get_workspace_agenda(
            self.db,
            self.technician,
            date(2026, 8, 7),
            date(2026, 8, 13),
            today=date(2026, 8, 7),
        )

        self.assertEqual({source_task.id, replacement_task.id}, {item.task_id for item in result.items})
        self.assertEqual("paused", next(item for item in result.items if item.task_id == source_task.id).slot_status)
        self.assertEqual("running", next(item for item in result.items if item.task_id == replacement_task.id).slot_status)

    def test_returns_running_activity_that_started_before_today(self):
        task = self.db.query(Task).filter(Task.name == "方法开发").one()
        slot = self.db.query(TimeSlot).one()
        task.status = "running"
        slot.status = "running"
        slot.plan_start = datetime(2026, 8, 10, 8, 30)
        slot.plan_end = datetime(2026, 8, 10, 12, 30)
        slot.actual_start = datetime(2026, 8, 7, 20, 17)
        self.db.commit()

        result = get_workspace_agenda(
            self.db,
            self.technician,
            date(2026, 8, 9),
            date(2026, 8, 15),
            today=date(2026, 8, 9),
        )

        self.assertEqual(1, len(result.items))
        self.assertEqual(task.id, result.items[0].task_id)
        self.assertEqual("running", result.items[0].execution_status)

    def test_agenda_uses_open_execution_segment_as_running_status(self):
        task = self.db.query(Task).filter(Task.name == "方法开发").one()
        slot = self.db.query(TimeSlot).one()
        task.status = "running"
        slot.status = "scheduled"
        slot.plan_start = datetime(2026, 8, 10, 8, 30)
        slot.plan_end = datetime(2026, 8, 10, 20)
        self.db.add(TaskExecutionSegment(
            task_id=task.id,
            slot_id=slot.id,
            instrument_id=self.instrument.id,
            operator_id=self.technician.id,
            started_at=datetime(2026, 8, 3, 10, 27),
        ))
        self.db.commit()

        result = get_workspace_agenda(
            self.db,
            self.technician,
            date(2026, 8, 10),
            date(2026, 8, 10),
        )

        self.assertEqual(1, len(result.items))
        self.assertEqual("scheduled", result.items[0].slot_status)
        self.assertEqual("running", result.items[0].execution_status)

    def test_manager_can_select_another_active_user(self):
        result = get_workspace_agenda(
            self.db,
            self.manager,
            date(2026, 8, 10),
            date(2026, 8, 10),
            self.technician.id,
        )

        self.assertTrue(result.can_select_assignee)
        self.assertEqual(self.technician.id, result.assignee.id)

    def test_technician_cannot_select_another_user(self):
        with self.assertRaisesRegex(WorkspaceAgendaPermissionError, "无权查看"):
            get_workspace_agenda(
                self.db,
                self.technician,
                date(2026, 8, 10),
                date(2026, 8, 10),
                self.manager.id,
            )

    def test_rejects_inactive_assignee_and_invalid_range(self):
        self.technician.is_active = False
        self.db.commit()
        with self.assertRaisesRegex(WorkspaceAgendaInvalidError, "已停用"):
            get_workspace_agenda(
                self.db,
                self.manager,
                date(2026, 8, 10),
                date(2026, 8, 10),
                self.technician.id,
            )
        with self.assertRaisesRegex(WorkspaceAgendaInvalidError, "不能晚于"):
            get_workspace_agenda(
                self.db,
                self.manager,
                date(2026, 8, 11),
                date(2026, 8, 10),
            )

    def test_rejects_ranges_longer_than_ninety_days(self):
        with self.assertRaisesRegex(WorkspaceAgendaInvalidError, "不能超过 90 天"):
            get_workspace_agenda(
                self.db,
                self.technician,
                date(2026, 1, 1),
                date(2026, 4, 1),
            )


if __name__ == "__main__":
    unittest.main()
