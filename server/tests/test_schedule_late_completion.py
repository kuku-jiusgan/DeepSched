import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot, User
from app.services.schedule_completion_service import complete_task_and_shift


class ScheduleLateCompletionTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_shifts_following_task_for_same_assignee(self):
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

        result = self._complete(completed.id, datetime(2026, 7, 13, 10, 27))

        shifted = self._active_slot(following.id)
        self.assertEqual(datetime(2026, 7, 13, 10, 30), shifted.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 18, 30), shifted.plan_end)
        self.db.refresh(following)
        self.assertEqual("not_delayed", following.delay_status)
        self.assertEqual(1, result["delay_affected_tasks"])
        self.assertEqual(0, result["moved_tasks"])

    def test_keeps_reported_delay_on_blocked_following_task(self):
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

        self._complete(completed.id, datetime(2026, 7, 13, 10, 27))

        self.db.refresh(following)
        self.assertEqual(datetime(2026, 7, 13, 10, 30), self._active_slot(following.id).plan_start)
        self.assertEqual("delayed", following.delay_status)

    def test_keeps_late_completion_when_following_task_cannot_shift(self):
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

        result = self._complete(completed.id, datetime(2026, 7, 13, 10, 0))

        completed_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == completed.id,
        ).one()
        self.assertEqual("completed", completed_slot.status)
        self.assertEqual(datetime(2026, 7, 13, 10, 0), completed_slot.actual_end)
        self.assertEqual(datetime(2026, 7, 13, 9, 0), self._active_slot(following.id).plan_start)
        self.assertIn("无法自动顺延", result["message"])

    def _active_slot(self, task_id: int) -> TimeSlot:
        return self.db.query(TimeSlot).filter(
            TimeSlot.task_id == task_id,
            TimeSlot.lifecycle_status == "active",
        ).one()

    def _complete(self, task_id: int, completed_at: datetime) -> dict:
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
                self.db, task_id, actual_end_time=completed_at,
            )


if __name__ == "__main__":
    unittest.main()
