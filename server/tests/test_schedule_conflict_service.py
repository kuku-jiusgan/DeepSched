import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.schedule_conflict_service import (
    ScheduleConflictError,
    ensure_no_human_conflicts,
    ensure_no_instrument_conflicts,
    find_human_conflicts,
    find_instrument_conflicts,
)


class ScheduleConflictServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def _create_task(self, task_id, assignee_id, requires_human=True):
        project = self.db.get(Project, 1)
        if not project:
            project = Project(id=1, name="测试项目", code="TEST-001")
            self.db.add(project)
        task = Task(
            id=task_id,
            project_id=project.id,
            name=f"任务{task_id}",
            task_type="test",
            requires_human=requires_human,
            assignee_id=assignee_id,
        )
        self.db.add(task)
        return task

    def _create_user(self, user_id):
        self.db.add(User(
            id=user_id,
            username=f"user{user_id}",
            display_name=f"人员{user_id}",
            role="分析员",
        ))

    def test_unexecuted_completed_segments_do_not_create_conflicts(self):
        completed_future = TimeSlot(
            task_id=1, instrument_id=1,
            plan_start=datetime(2026, 7, 16, 8, 30),
            plan_end=datetime(2026, 7, 16, 17, 30),
            status="completed",
        )
        scheduled = TimeSlot(
            task_id=2, instrument_id=1,
            plan_start=datetime(2026, 7, 16, 14, 30),
            plan_end=datetime(2026, 7, 16, 20, 0),
            status="scheduled",
        )
        self.db.add_all([completed_future, scheduled])
        self.db.commit()

        self.assertEqual([], find_instrument_conflicts(self.db))

    def test_completed_segments_use_actual_time_for_conflicts(self):
        completed = TimeSlot(
            task_id=1, instrument_id=1,
            plan_start=datetime(2026, 7, 16, 8, 30),
            plan_end=datetime(2026, 7, 16, 17, 30),
            actual_start=datetime(2026, 7, 16, 8, 30),
            actual_end=datetime(2026, 7, 16, 12, 30),
            status="completed",
        )
        scheduled = TimeSlot(
            task_id=2, instrument_id=1,
            plan_start=datetime(2026, 7, 16, 14, 30),
            plan_end=datetime(2026, 7, 16, 20, 0),
            status="scheduled",
        )
        self.db.add_all([completed, scheduled])
        self.db.commit()

        self.assertEqual([], find_instrument_conflicts(self.db))

    def test_running_segment_uses_actual_start_instead_of_stale_plan_start(self):
        completed = TimeSlot(
            task_id=1, instrument_id=1,
            plan_start=datetime(2026, 7, 20, 8, 30),
            plan_end=datetime(2026, 7, 20, 12, 0),
            actual_start=datetime(2026, 7, 20, 8, 30),
            actual_end=datetime(2026, 7, 20, 14, 5),
            status="completed",
        )
        running = TimeSlot(
            task_id=2, instrument_id=1,
            plan_start=datetime(2026, 7, 20, 12, 30),
            plan_end=datetime(2026, 7, 21, 6, 0),
            actual_start=datetime(2026, 7, 20, 14, 17),
            status="running",
        )
        self.db.add_all([completed, running])
        self.db.commit()

        self.assertEqual([], find_instrument_conflicts(self.db))

    def test_segments_of_same_running_task_do_not_conflict_with_each_other(self):
        self.db.add_all([
            TimeSlot(
                task_id=1, instrument_id=1,
                plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 22, 0),
                actual_start=datetime(2026, 7, 16, 9, 30),
                status="running",
            ),
            TimeSlot(
                task_id=1, instrument_id=1,
                plan_start=datetime(2026, 7, 17, 8, 30),
                plan_end=datetime(2026, 7, 17, 22, 0),
                status="running",
            ),
        ])
        self.db.commit()

        self.assertEqual([], find_instrument_conflicts(self.db))

    def test_historical_actual_overlap_does_not_block_future_scheduling(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1)
        self.db.add_all([
            TimeSlot(
                task_id=1,
                actual_start=datetime(2026, 7, 20, 8, 30),
                actual_end=datetime(2026, 7, 20, 14, 5),
                plan_start=datetime(2026, 7, 20, 8, 30),
                plan_end=datetime(2026, 7, 20, 12, 0),
                status="completed",
            ),
            TimeSlot(
                task_id=2,
                actual_start=datetime(2026, 7, 20, 9, 30),
                plan_start=datetime(2026, 7, 20, 14, 30),
                plan_end=datetime(2026, 7, 20, 22, 0),
                status="running",
            ),
        ])
        self.db.commit()

        self.assertEqual([], find_human_conflicts(self.db))

    def test_instrument_conflict_message_uses_business_details(self):
        self._create_task(1, None, requires_human=False)
        self._create_task(2, None, requires_human=False)
        self.db.add(Instrument(id=1, code="GCMS-001", name="气相质谱仪"))
        self.db.add_all([
            TimeSlot(
                task_id=1,
                instrument_id=1,
                plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0),
                status="scheduled",
            ),
            TimeSlot(
                task_id=2,
                instrument_id=1,
                plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0),
                status="scheduled",
            ),
        ])
        self.db.commit()

        with self.assertRaises(ScheduleConflictError) as context:
            ensure_no_instrument_conflicts(self.db)

        message = str(context.exception)
        self.assertIn("仪器【GCMS-001 气相质谱仪】", message)
        self.assertIn("项目【TEST-001 测试项目】任务【任务1】", message)
        self.assertIn("项目【TEST-001 测试项目】任务【任务2】", message)
        self.assertIn("冲突时段为【2026-07-16 10:00 至 2026-07-16 12:00】", message)
        self.assertNotIn("时间槽", message)

    def test_overlapping_human_tasks_for_same_assignee_create_conflict(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1)
        self.db.add_all([
            TimeSlot(
                task_id=1, plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=2, plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        conflicts = find_human_conflicts(self.db)

        self.assertEqual(1, len(conflicts))
        self.assertEqual(1, conflicts[0]["assignee_id"])

    def test_human_conflict_message_uses_business_details(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1)
        self.db.add_all([
            TimeSlot(
                task_id=1,
                plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0),
                status="scheduled",
            ),
            TimeSlot(
                task_id=2,
                plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0),
                status="scheduled",
            ),
        ])
        self.db.commit()

        with self.assertRaises(ScheduleConflictError) as context:
            ensure_no_human_conflicts(self.db)

        message = str(context.exception)
        self.assertIn("负责人【人员1】", message)
        self.assertIn("项目【TEST-001 测试项目】任务【任务1】", message)
        self.assertIn("项目【TEST-001 测试项目】任务【任务2】", message)
        self.assertIn("2026-07-16 08:30 至 2026-07-16 12:00", message)
        self.assertIn("冲突时段为【2026-07-16 10:00 至 2026-07-16 12:00】", message)
        self.assertNotIn("时间槽", message)

    def test_different_assignees_do_not_create_human_conflict(self):
        self._create_user(1)
        self._create_user(2)
        self._create_task(1, 1)
        self._create_task(2, 2)
        self.db.add_all([
            TimeSlot(
                task_id=1, plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=2, plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        self.assertEqual([], find_human_conflicts(self.db))

    def test_non_human_task_does_not_create_human_conflict(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1, requires_human=False)
        self.db.add_all([
            TimeSlot(
                task_id=1, plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=2, plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        self.assertEqual([], find_human_conflicts(self.db))

    def test_unexecuted_completed_segment_does_not_create_human_conflict(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1)
        self.db.add_all([
            TimeSlot(
                task_id=1, plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0), status="completed",
            ),
            TimeSlot(
                task_id=2, plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        self.assertEqual([], find_human_conflicts(self.db))

    def test_completed_actual_time_creates_human_conflict(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1)
        self.db.add_all([
            TimeSlot(
                task_id=1, plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0),
                actual_start=datetime(2026, 7, 16, 9, 0),
                actual_end=datetime(2026, 7, 16, 11, 0), status="completed",
            ),
            TimeSlot(
                task_id=2, plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        self.assertEqual(1, len(find_human_conflicts(self.db)))

    def test_existing_conflict_outside_current_run_does_not_block_scheduling(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1)
        self._create_task(3, 1)
        self.db.add_all([
            TimeSlot(
                task_id=1, schedule_run_id="old-a",
                plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=2, schedule_run_id="old-b",
                plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=3, schedule_run_id="current",
                plan_start=datetime(2026, 7, 17, 8, 30),
                plan_end=datetime(2026, 7, 17, 10, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        ensure_no_human_conflicts(self.db, "current")

    def test_conflict_with_current_run_still_blocks_scheduling(self):
        self._create_user(1)
        self._create_task(1, 1)
        self._create_task(2, 1)
        self.db.add_all([
            TimeSlot(
                task_id=1, schedule_run_id="old",
                plan_start=datetime(2026, 7, 16, 8, 30),
                plan_end=datetime(2026, 7, 16, 12, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=2, schedule_run_id="current",
                plan_start=datetime(2026, 7, 16, 10, 0),
                plan_end=datetime(2026, 7, 16, 14, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        with self.assertRaises(ScheduleConflictError):
            ensure_no_human_conflicts(self.db, "current")


if __name__ == "__main__":
    unittest.main()
