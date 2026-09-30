import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot
from app.services.task_schedule_staleness_service import mark_expired_paused_tasks


class TaskScheduleStalenessTest(unittest.TestCase):
    def setUp(self):
        self.db = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
        Base.metadata.create_all(self.db.bind)
        self.now = datetime(2026, 9, 20, 8, 0)
        self.project = Project(code="P", name="项目")
        self.db.add(self.project)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def _task(self, **kwargs):
        task = Task(
            project_id=self.project.id, name="任务", task_type="test",
            status="paused", est_duration_hours=8, **kwargs,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _slot(self, task, **kwargs):
        values = {
            "plan_start": self.now - timedelta(days=2),
            "plan_end": self.now - timedelta(days=1),
        }
        values.update(kwargs)
        slot = TimeSlot(
            task_id=task.id, status="scheduled", lifecycle_status="active",
            **values,
        )
        self.db.add(slot)
        self.db.flush()
        return slot

    def test_marks_paused_task_with_only_expired_slots(self):
        task = self._task()
        self._slot(task)

        stale = mark_expired_paused_tasks(self.db, self.project.id, self.now)

        self.assertEqual({task.id}, stale)
        self.assertTrue(task.schedule_dirty)

    def test_does_not_mark_completed_work_or_future_slot(self):
        completed = self._task(executed_minutes=480)
        self._slot(completed)
        future = self._task()
        self._slot(
            future,
            plan_start=self.now + timedelta(hours=1),
            plan_end=self.now + timedelta(hours=3),
        )

        stale = mark_expired_paused_tasks(self.db, self.project.id, self.now)

        self.assertEqual(set(), stale)
        self.assertFalse(completed.schedule_dirty)
        self.assertFalse(future.schedule_dirty)


if __name__ == "__main__":
    unittest.main()
