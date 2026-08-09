import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schedules import (
    _filter_workspace_tasks_by_user,
    _select_workspace_slot,
    _should_include_delay_fields,
    _task_actual_window,
)
from app.core.database import Base
from app.models import AuditLog, Project, Task, TaskExecutionSegment, TimeSlot, User
from app.services.workspace_service import get_workspace_tasks


class WorkspaceTaskVisibilityTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.admin = User(username="admin", display_name="系统管理员", role="系统管理员", is_active=True)
        self.project_admin = User(username="project-admin", display_name="项目管理员", role="项目管理员", is_active=True)
        self.owner = User(username="owner", display_name="李伟", role="分析员", is_active=True)
        self.other = User(username="other", display_name="陈婷婷", role="分析员", is_active=True)
        self.db.add_all([self.admin, self.project_admin, self.owner, self.other])
        self.db.flush()

        project = Project(name="项目", code="XM-001", manager_id=self.owner.id)
        self.db.add(project)
        self.db.flush()

        self.owner_task = Task(
            project_id=project.id,
            name="负责人任务",
            task_type="FFKF_001",
            status="scheduled",
            assignee_id=self.owner.id,
        )
        self.other_task = Task(
            project_id=project.id,
            name="其他人任务",
            task_type="FFKF_001",
            status="scheduled",
            assignee_id=self.other.id,
        )
        self.db.add_all([self.owner_task, self.other_task])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_system_admin_sees_all_workspace_tasks(self):
        query = _filter_workspace_tasks_by_user(self.db.query(Task), self.admin)

        self.assertEqual({self.owner_task.id, self.other_task.id}, {task.id for task in query.all()})

    def test_regular_user_only_sees_assigned_workspace_tasks(self):
        query = _filter_workspace_tasks_by_user(self.db.query(Task), self.owner)

        self.assertEqual([self.owner_task.id], [task.id for task in query.all()])

    def test_project_admin_sees_all_workspace_tasks(self):
        query = _filter_workspace_tasks_by_user(self.db.query(Task), self.project_admin)

        self.assertEqual({self.owner_task.id, self.other_task.id}, {task.id for task in query.all()})

    def test_workspace_slot_uses_current_running_segment(self):
        past_slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=1,
            plan_start=datetime(2026, 7, 13, 19, 0),
            plan_end=datetime(2026, 7, 13, 22, 0),
            status="running",
        )
        today_slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=1,
            plan_start=datetime(2026, 7, 14, 8, 30),
            plan_end=datetime(2026, 7, 14, 22, 0),
            status="running",
        )
        future_slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=1,
            plan_start=datetime(2026, 7, 15, 8, 30),
            plan_end=datetime(2026, 7, 15, 12, 30),
            status="running",
        )

        selected = _select_workspace_slot(
            [past_slot, today_slot, future_slot],
            datetime(2026, 7, 14, 9, 0),
        )

        self.assertIs(today_slot, selected)

    def test_task_actual_window_uses_earliest_start_across_segments(self):
        started_at = datetime(2026, 7, 13, 19, 3, 58)
        slots = [
            TimeSlot(
                task_id=self.owner_task.id,
                instrument_id=1,
                plan_start=datetime(2026, 7, 13, 19, 0),
                plan_end=datetime(2026, 7, 13, 22, 0),
                actual_start=started_at,
                status="running",
            ),
            TimeSlot(
                task_id=self.owner_task.id,
                instrument_id=1,
                plan_start=datetime(2026, 7, 14, 8, 30),
                plan_end=datetime(2026, 7, 15, 6, 0),
                status="running",
            ),
            TimeSlot(
                task_id=self.owner_task.id,
                instrument_id=1,
                plan_start=datetime(2026, 7, 15, 8, 30),
                plan_end=datetime(2026, 7, 15, 12, 30),
                status="running",
            ),
        ]

        actual_start, actual_end = _task_actual_window(slots)

        self.assertEqual(started_at, actual_start)
        self.assertIsNone(actual_end)

    def test_workspace_response_includes_persisted_delay_status(self):
        self.owner_task.delay_status = "delayed"
        self.db.add(TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=1,
            plan_start=datetime(2099, 7, 17, 12, 30),
            plan_end=datetime(2099, 7, 17, 13, 0),
            status="scheduled",
        ))
        self.db.commit()

        result = get_workspace_tasks(self.db, self.owner)

        task_result = next(item for item in result if item.task_id == self.owner_task.id)
        self.assertEqual("delayed", task_result.delay.status)

    def test_workspace_response_identifies_paused_task_to_resume(self):
        self.owner_task.status = "running"
        self.other_task.status = "paused"
        self.db.add(AuditLog(
            user_name="李伟",
            action="task_paused",
            target_type="task",
            target_id=self.other_task.id,
            detail={
                "source_task_id": self.other_task.id,
                "source_slot_id": 10,
                "target_task_id": self.owner_task.id,
                "target_slot_id": 20,
            },
        ))
        self.db.commit()

        result = get_workspace_tasks(self.db, self.owner)

        task_result = next(item for item in result if item.task_id == self.owner_task.id)
        self.assertEqual(self.other_task.id, task_result.resume_priority.task_id)
        self.assertEqual("其他人任务", task_result.resume_priority.task_name)
        self.assertEqual("XM-001", task_result.resume_priority.project_code)
        self.assertEqual("项目", task_result.resume_priority.project_name)

    def test_normal_task_does_not_expose_old_delay_detail(self):
        slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=1,
            plan_start=datetime(2026, 7, 24, 12, 30),
            plan_end=datetime(2026, 8, 3, 13, 30),
            status="scheduled",
        )
        self.db.add(slot)
        self.db.flush()
        self.db.add(AuditLog(
            user_name="系统管理员",
            action="task_delay_reported",
            target_type="time_slot",
            target_id=slot.id,
            detail={"task_id": self.owner_task.id, "delay_hours": 2, "reason": "旧延期"},
        ))
        self.owner_task.delay_status = "not_delayed"
        self.db.commit()

        result = get_workspace_tasks(self.db, self.owner, datetime(2026, 7, 24, 11, 30))

        task_result = next(item for item in result if item.task_id == self.owner_task.id)
        self.assertEqual("not_delayed", task_result.delay.status)
        self.assertIsNone(task_result.delay.hours)
        self.assertIsNone(task_result.delay.reason)

    def test_completed_slot_keeps_explicit_delay_detail_for_gantt(self):
        slot = TimeSlot(
            task_id=self.owner_task.id,
            plan_start=datetime(2026, 7, 17, 8, 30),
            plan_end=datetime(2026, 7, 17, 21, 0),
            status="completed",
        )

        self.assertTrue(_should_include_delay_fields(slot))

    def test_workspace_uses_full_task_window_across_rest_periods(self):
        first_slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=None,
            plan_start=datetime(2026, 7, 17, 21, 0),
            plan_end=datetime(2026, 7, 17, 22, 0),
            status="scheduled",
        )
        second_slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=None,
            plan_start=datetime(2026, 7, 20, 8, 30),
            plan_end=datetime(2026, 7, 20, 9, 30),
            status="scheduled",
        )
        self.db.add_all([first_slot, second_slot])
        self.db.commit()

        result = get_workspace_tasks(self.db, self.owner, datetime(2026, 7, 18, 9, 0))

        task_result = next(item for item in result if item.task_id == self.owner_task.id)
        self.assertEqual(datetime(2026, 7, 17, 21, 0), task_result.task_window.start)
        self.assertEqual(datetime(2026, 7, 20, 9, 30), task_result.task_window.end)
        self.assertEqual(second_slot.id, task_result.actionable_slot.id)
        self.assertEqual(2, len(task_result.segments))

    def test_incomplete_paused_task_is_reported_as_interrupted(self):
        self.owner_task.status = "paused"
        slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=None,
            plan_start=datetime(2026, 7, 17, 21, 0),
            plan_end=datetime(2026, 7, 17, 22, 0),
            actual_start=datetime(2026, 7, 17, 21, 5),
            actual_end=None,
            status="paused",
        )
        self.db.add(slot)
        self.db.flush()
        self.db.add(TaskExecutionSegment(
            task_id=self.owner_task.id,
            slot_id=slot.id,
            instrument_id=None,
            operator_id=self.owner.id,
            started_at=datetime(2026, 7, 17, 21, 5),
            ended_at=None,
        ))
        self.db.commit()

        result = get_workspace_tasks(self.db, self.owner, datetime(2026, 7, 17, 21, 30))

        task_result = next(item for item in result if item.task_id == self.owner_task.id)
        self.assertEqual("interrupted", task_result.execution_status)

    def test_workspace_status_follows_paused_actionable_slot_without_running_segments(self):
        self.owner_task.status = "running"
        slot = TimeSlot(
            task_id=self.owner_task.id,
            instrument_id=1,
            plan_start=datetime(2026, 8, 3, 15, 0),
            plan_end=datetime(2026, 8, 12, 11, 30),
            actual_start=datetime(2026, 8, 3, 14, 59),
            actual_end=datetime(2026, 8, 6, 15, 40),
            status="paused",
        )
        self.db.add(slot)
        self.db.commit()

        result = get_workspace_tasks(self.db, self.owner, datetime(2026, 8, 7, 10, 0))

        task_result = next(item for item in result if item.task_id == self.owner_task.id)
        self.assertEqual("paused", task_result.execution_status)
        self.assertEqual("paused", task_result.actionable_slot.status)


if __name__ == "__main__":
    unittest.main()
