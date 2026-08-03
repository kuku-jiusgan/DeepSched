import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Notification, Project, Task, TaskDependency, TimeSlot, User
from app.services.schedule_completion_service import (
    _forward_shift_instrument_queue,
    _mark_task_slots_completed,
    _select_completed_slot,
    complete_task_and_shift,
)


class ScheduleCompletionTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_complete_multi_day_task_preserves_plan_boundaries(self):
        task = Task(project_id=1, name="multi-day", task_type="test", status="running")
        self.db.add(task)
        self.db.flush()
        slots = [
            TimeSlot(
                task_id=task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 10, 17, 30),
                plan_end=datetime(2026, 7, 10, 20, 0), status="running",
            ),
            TimeSlot(
                task_id=task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 11, 8, 30),
                plan_end=datetime(2026, 7, 11, 20, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 12, 8, 30),
                plan_end=datetime(2026, 7, 12, 17, 0), status="scheduled",
            ),
        ]
        self.db.add_all(slots)
        self.db.commit()
        original_ranges = [(slot.plan_start, slot.plan_end) for slot in slots]
        end_time = datetime(2026, 7, 13, 9, 23)

        completed_slot = _select_completed_slot(slots, slots[0].id, end_time)
        _mark_task_slots_completed(slots, completed_slot, end_time)

        self.assertEqual(slots[-1].id, completed_slot.id)
        self.assertEqual(original_ranges, [(slot.plan_start, slot.plan_end) for slot in slots])
        self.assertTrue(all(slot.status == "completed" for slot in slots))
        self.assertEqual(end_time, slots[-1].actual_end)
        self.assertEqual(datetime(2026, 7, 10, 20, 0), slots[0].actual_end)
        self.assertEqual(datetime(2026, 7, 11, 20, 0), slots[1].actual_end)

    def test_future_segments_complete_without_fake_actual_times(self):
        slots = [
            TimeSlot(
                id=1, task_id=1, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 20, 0), status="running",
            ),
            TimeSlot(
                id=2, task_id=1, instrument_id=1,
                plan_start=datetime(2026, 7, 14, 8, 30),
                plan_end=datetime(2026, 7, 14, 18, 30), status="scheduled",
            ),
        ]
        end_time = datetime(2026, 7, 13, 10, 0)

        _mark_task_slots_completed(slots, slots[0], end_time)

        self.assertEqual("completed", slots[1].status)
        self.assertIsNone(slots[1].actual_start)
        self.assertIsNone(slots[1].actual_end)
        self.assertEqual(datetime(2026, 7, 14, 18, 30), slots[1].plan_end)

    def test_completed_future_segments_do_not_block_forward_shift(self):
        completed = Task(project_id=1, name="done", task_type="test", status="done")
        next_task = Task(project_id=1, name="next", task_type="test", status="scheduled")
        self.db.add_all([completed, next_task])
        self.db.flush()
        self.db.add(TaskDependency(task_id=next_task.id, predecessor_id=completed.id))
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 20, 0),
                actual_start=datetime(2026, 7, 13, 8, 30),
                actual_end=datetime(2026, 7, 13, 12, 0),
                status="completed",
            ),
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 14, 8, 30),
                plan_end=datetime(2026, 7, 14, 20, 0),
                status="completed",
            ),
            TimeSlot(
                task_id=next_task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 15, 8, 30),
                plan_end=datetime(2026, 7, 15, 10, 30),
                status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        moved_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == next_task.id,
            TimeSlot.status == "scheduled",
        ).one()
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(datetime(2026, 7, 13, 12, 0), moved_slot.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 14, 0), moved_slot.plan_end)

    def test_forward_shift_compacts_instrument_queue_across_projects(self):
        first = Task(project_id=2, name="project-b", task_type="test", status="scheduled")
        second = Task(project_id=3, name="project-c", task_type="test", status="scheduled")
        self.db.add_all([first, second])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=first.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 15, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=second.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 17, 0),
                plan_end=datetime(2026, 7, 13, 19, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        slots = self.db.query(TimeSlot).order_by(TimeSlot.plan_start).all()
        self.assertEqual(2, result["moved_tasks"])
        self.assertEqual([first.id, second.id], [slot.task_id for slot in slots])
        self.assertEqual(datetime(2026, 7, 13, 12, 0), slots[0].plan_start)
        self.assertEqual(datetime(2026, 7, 13, 14, 0), slots[1].plan_start)

    def test_forward_shift_moves_frozen_but_ignores_manual_and_running_tasks(self):
        manual = Task(project_id=1, name="manual", task_type="test", status="scheduled")
        frozen = Task(project_id=2, name="frozen", task_type="test", status="scheduled")
        partly_running = Task(
            project_id=3, name="partly-running", task_type="test", status="scheduled",
        )
        self.db.add_all([manual, frozen, partly_running])
        self.db.flush()
        original_start = datetime(2026, 7, 13, 16, 0)
        self.db.add_all([
            TimeSlot(
                task_id=manual.id, instrument_id=None,
                plan_start=datetime(2026, 7, 13, 14, 0),
                plan_end=datetime(2026, 7, 13, 15, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=frozen.id, instrument_id=1,
                plan_start=original_start,
                plan_end=datetime(2026, 7, 13, 18, 0),
                tier="frozen", status="scheduled",
            ),
            TimeSlot(
                task_id=partly_running.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 10, 0),
                plan_end=datetime(2026, 7, 13, 12, 0), status="running",
            ),
            TimeSlot(
                task_id=partly_running.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 18, 0),
                plan_end=datetime(2026, 7, 13, 19, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        frozen_slot = self.db.query(TimeSlot).filter(TimeSlot.task_id == frozen.id).one()
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(datetime(2026, 7, 13, 12, 0), frozen_slot.plan_start)
        self.assertNotEqual(original_start, frozen_slot.plan_start)
        self.assertEqual(1, self.db.query(TimeSlot).filter(TimeSlot.task_id == manual.id).count())
        self.assertEqual(
            2,
            self.db.query(TimeSlot).filter(TimeSlot.task_id == partly_running.id).count(),
        )

    def test_forward_shift_respects_dependency_and_human_availability(self):
        predecessor = Task(project_id=1, name="predecessor", task_type="test", status="scheduled")
        candidate = Task(
            project_id=2, name="candidate", task_type="test", status="scheduled",
            requires_human=True, assignee_id=7,
        )
        other_work = Task(
            project_id=3, name="other-work", task_type="test", status="scheduled",
            requires_human=True, assignee_id=7,
        )
        self.db.add_all([predecessor, candidate, other_work])
        self.db.flush()
        self.db.add(TaskDependency(task_id=candidate.id, predecessor_id=predecessor.id))
        self.db.add_all([
            TimeSlot(
                task_id=predecessor.id, instrument_id=None,
                plan_start=datetime(2026, 7, 13, 11, 0),
                plan_end=datetime(2026, 7, 13, 14, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=other_work.id, instrument_id=2,
                plan_start=datetime(2026, 7, 13, 14, 0),
                plan_end=datetime(2026, 7, 13, 15, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=candidate.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 17, 0),
                plan_end=datetime(2026, 7, 13, 19, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        moved_slot = self.db.query(TimeSlot).filter(TimeSlot.task_id == candidate.id).one()
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(datetime(2026, 7, 13, 15, 0), moved_slot.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 17, 0), moved_slot.plan_end)

    def test_early_completion_notifies_each_moved_task_assignee(self):
        assignee = User(
            username="analyst",
            display_name="任务负责人",
            role="分析员",
            is_active=True,
        )
        completed = Task(project_id=1, name="前序检测", task_type="test", status="running")
        moved = Task(
            project_id=2,
            name="后续检测",
            task_type="test",
            status="scheduled",
            requires_human=False,
            assignee=assignee,
        )
        self.db.add_all([assignee, completed, moved])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 14, 0), status="running",
            ),
            TimeSlot(
                task_id=moved.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 15, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 12, 0))

        notifications = self.db.query(Notification).order_by(Notification.id).all()
        notification = next(item for item in notifications if item.channel == "site")
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(["site", "wecom"], [item.channel for item in notifications])
        self.assertEqual("analyst", notification.user_name)
        self.assertEqual("task_schedule_advanced", notification.n_type)
        self.assertIn("前序检测", notification.content)
        self.assertIn("2026-07-13 12:00", notification.content)
        self.assertIn("2026-07-13 15:00", notification.content)

    def test_on_time_completion_does_not_send_advance_notification(self):
        assignee = User(
            username="analyst",
            display_name="任务负责人",
            role="分析员",
            is_active=True,
        )
        completed = Task(project_id=1, name="前序检测", task_type="test", status="running")
        moved = Task(
            project_id=2,
            name="后续检测",
            task_type="test",
            status="scheduled",
            requires_human=False,
            assignee=assignee,
        )
        self.db.add_all([assignee, completed, moved])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 14, 0), status="running",
            ),
            TimeSlot(
                task_id=moved.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 15, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 14, 0))

        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(0, self.db.query(Notification).count())

    def test_late_completion_shifts_following_task_for_same_assignee(self):
        project = Project(
            id=1, name="延期项目", code="DELAY-1",
            start_date=datetime(2026, 7, 13),
            end_date=datetime(2026, 7, 20, 23, 59),
        )
        assignee = User(
            id=7, username="analyst-7", display_name="负责人",
            role="分析员", is_active=True,
        )
        completed = Task(
            project=project, name="方法验证", task_type="test", status="running",
            requires_human=True, assignee=assignee,
        )
        following = Task(
            project_id=2, name="报告撰写", task_type="manual", status="scheduled",
            delay_status="delayed", requires_human=True, assignee=assignee,
        )
        self.db.add_all([project, assignee, completed, following])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 9, 0), status="running",
            ),
            TimeSlot(
                task_id=following.id, plan_start=datetime(2026, 7, 13, 9, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 10, 27))

        shifted = self.db.query(TimeSlot).filter(TimeSlot.task_id == following.id).one()
        self.assertEqual(datetime(2026, 7, 13, 10, 30), shifted.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 18, 30), shifted.plan_end)
        self.db.refresh(following)
        self.assertEqual("not_delayed", following.delay_status)
        self.assertEqual(1, result["delay_affected_tasks"])
        self.assertEqual(0, result["moved_tasks"])

    def test_late_completion_keeps_reported_delay_on_blocked_following_task(self):
        project = Project(
            id=1, name="延期项目", code="DELAY-BLOCKED",
            end_date=datetime(2026, 7, 20, 23, 59),
        )
        completed = Task(
            project=project, name="方法验证", task_type="test", status="running",
        )
        following = Task(
            project=project, name="报告撰写", task_type="manual", status="blocked",
            delay_status="delayed",
        )
        self.db.add_all([project, completed, following])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 9, 0), status="running",
            ),
            TimeSlot(
                task_id=following.id, plan_start=datetime(2026, 7, 13, 9, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="blocked",
            ),
        ])
        self.db.commit()

        self._complete_and_shift(completed.id, datetime(2026, 7, 13, 10, 27))

        self.db.refresh(following)
        shifted = self.db.query(TimeSlot).filter(TimeSlot.task_id == following.id).one()
        self.assertEqual(datetime(2026, 7, 13, 10, 30), shifted.plan_start)
        self.assertEqual("delayed", following.delay_status)

    def test_late_completion_is_kept_when_following_task_cannot_shift(self):
        project = Project(
            id=1, name="截止项目", code="DELAY-END",
            end_date=datetime(2026, 7, 13, 17, 30),
        )
        completed = Task(
            project=project, name="方法验证", task_type="test", status="running",
        )
        following = Task(
            project=project, name="报告撰写", task_type="manual", status="scheduled",
        )
        self.db.add_all([project, completed, following])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 9, 0), status="running",
            ),
            TimeSlot(
                task_id=following.id, plan_start=datetime(2026, 7, 13, 9, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 10, 0))

        completed_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == completed.id,
        ).one()
        following_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following.id,
        ).one()
        self.assertEqual("completed", completed_slot.status)
        self.assertEqual(datetime(2026, 7, 13, 10, 0), completed_slot.actual_end)
        self.assertEqual(datetime(2026, 7, 13, 9, 0), following_slot.plan_start)
        self.assertIn("无法自动顺延", result["message"])

    def _forward_shift(self, instrument_id: int, released_at: datetime) -> dict:
        working_options = {
            "day_start_minutes": 8 * 60 + 30,
            "day_end_minutes": 20 * 60,
            "include_weekends": True,
            "include_holidays": True,
            "horizon_end": datetime(2026, 7, 20),
            "calendar_days": {},
        }
        with patch(
            "app.services.schedule_completion_service._load_working_options",
            return_value=working_options,
        ):
            return _forward_shift_instrument_queue(self.db, instrument_id, released_at)

    def _complete_and_shift(self, task_id: int, completed_at: datetime) -> dict:
        working_options = {
            "day_start_minutes": 8 * 60 + 30,
            "day_end_minutes": 20 * 60,
            "include_weekends": True,
            "include_holidays": True,
            "horizon_end": datetime(2026, 7, 20),
            "calendar_days": {},
        }
        with patch(
            "app.services.schedule_completion_service._load_working_options",
            return_value=working_options,
        ):
            return complete_task_and_shift(
                self.db,
                task_id,
                actual_end_time=completed_at,
            )


if __name__ == "__main__":
    unittest.main()
