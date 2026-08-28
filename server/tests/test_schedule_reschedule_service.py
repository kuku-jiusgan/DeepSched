import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot
from app.services.schedule_reschedule_service import reschedule


class ScheduleRescheduleServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_global_reschedule_failure_restores_existing_schedule(self):
        project = Project(name="全局重排测试", code="GLOBAL-ROLLBACK")
        self.db.add(project)
        self.db.flush()
        task = Task(
            project_id=project.id,
            name="原排程任务",
            task_type="test",
            requires_human=False,
            status="scheduled",
        )
        self.db.add(task)
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id,
            plan_start=datetime(2026, 8, 3, 8, 30),
            plan_end=datetime(2026, 8, 3, 9, 30),
            tier="confirmed",
            status="scheduled",
        )
        self.db.add(slot)
        self.db.commit()
        request = type("Request", (), {"strategy": "global"})()

        with patch(
            "app.services.scheduler.SchedulerService.generate",
            return_value={"status": "error", "message": "求解失败"},
        ):
            result = reschedule(self.db, request)

        self.assertEqual("error", result["status"])
        self.assertEqual("scheduled", self.db.get(Task, task.id).status)
        self.assertIsNotNone(self.db.get(TimeSlot, slot.id))

    def test_project_reschedule_failure_restores_existing_schedule(self):
        project = Project(name="项目回滚测试", code="PROJECT-ROLLBACK")
        self.db.add(project)
        self.db.flush()
        task = Task(
            project_id=project.id, name="原排程任务", task_type="test",
            requires_human=False, status="scheduled",
        )
        self.db.add(task)
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id, plan_start=datetime(2026, 8, 3, 8, 30),
            plan_end=datetime(2026, 8, 3, 9, 30), tier="confirmed",
            status="scheduled",
        )
        self.db.add(slot)
        self.db.commit()
        request = type("Request", (), {
            "strategy": "project", "affected_task_id": task.id,
        })()

        with patch(
            "app.services.scheduler.SchedulerService.generate",
            return_value={"status": "error", "message": "求解失败"},
        ):
            result = reschedule(self.db, request)

        self.assertEqual("error", result["status"])
        self.assertEqual("scheduled", self.db.get(Task, task.id).status)
        restored = self.db.get(TimeSlot, slot.id)
        self.assertEqual("scheduled", restored.status)
        self.assertEqual("active", restored.lifecycle_status)

    def test_global_reschedule_preserves_and_excludes_locked_tasks(self):
        project = Project(name="锁定任务测试", code="GLOBAL-LOCKED")
        self.db.add(project)
        self.db.flush()
        frozen_task = Task(
            project_id=project.id,
            name="冻结任务",
            task_type="test",
            requires_human=False,
            status="pending",
        )
        running_task = Task(
            project_id=project.id,
            name="运行中任务",
            task_type="test",
            requires_human=False,
            status="running",
        )
        completed_task = Task(
            project_id=project.id,
            name="已完成任务",
            task_type="test",
            requires_human=False,
            status="completed",
        )
        movable_task = Task(
            project_id=project.id,
            name="可移动任务",
            task_type="test",
            requires_human=False,
            status="scheduled",
        )
        self.db.add_all([
            frozen_task,
            running_task,
            completed_task,
            movable_task,
        ])
        self.db.flush()
        slots = [
            TimeSlot(
                task_id=frozen_task.id,
                plan_start=datetime(2026, 8, 3, 8, 30),
                plan_end=datetime(2026, 8, 3, 9, 30),
                tier="frozen",
                status="scheduled",
            ),
            TimeSlot(
                task_id=frozen_task.id,
                plan_start=datetime(2026, 8, 4, 8, 30),
                plan_end=datetime(2026, 8, 4, 9, 30),
                tier="confirmed",
                status="scheduled",
            ),
            TimeSlot(
                task_id=running_task.id,
                plan_start=datetime(2026, 8, 5, 8, 30),
                plan_end=datetime(2026, 8, 5, 9, 30),
                tier="confirmed",
                status="running",
            ),
            TimeSlot(
                task_id=completed_task.id,
                plan_start=datetime(2026, 8, 6, 8, 30),
                plan_end=datetime(2026, 8, 6, 9, 30),
                tier="forecast",
                status="completed",
            ),
            TimeSlot(
                task_id=movable_task.id,
                plan_start=datetime(2026, 8, 7, 8, 30),
                plan_end=datetime(2026, 8, 7, 9, 30),
                tier="confirmed",
                status="scheduled",
            ),
        ]
        self.db.add_all(slots)
        self.db.commit()
        slot_ids = [slot.id for slot in slots]
        frozen_task_id = frozen_task.id
        running_task_id = running_task.id
        completed_task_id = completed_task.id
        movable_task_id = movable_task.id
        request = type("Request", (), {"strategy": "global"})()
        captured_excluded_task_ids = set()

        def generate_result(*args, **kwargs):
            captured_excluded_task_ids.update(kwargs["excluded_task_ids"])
            return {"status": "ok", "timeslots_created": 0}

        with patch(
            "app.services.scheduler.SchedulerService.generate",
            side_effect=generate_result,
        ):
            result = reschedule(self.db, request)

        self.assertEqual("ok", result["status"])
        self.assertEqual(
            {frozen_task_id, running_task_id, completed_task_id},
            captured_excluded_task_ids,
        )
        self.assertIsNotNone(self.db.get(TimeSlot, slot_ids[0]))
        self.assertIsNotNone(self.db.get(TimeSlot, slot_ids[1]))
        self.assertIsNotNone(self.db.get(TimeSlot, slot_ids[2]))
        self.assertIsNotNone(self.db.get(TimeSlot, slot_ids[3]))
        superseded_slot = self.db.get(TimeSlot, slot_ids[4])
        self.assertIsNotNone(superseded_slot)
        self.assertEqual("cancelled", superseded_slot.status)
        self.assertEqual("superseded", superseded_slot.lifecycle_status)
        self.assertEqual("排程重排", superseded_slot.superseded_reason)
        self.assertEqual("pending", self.db.get(Task, movable_task_id).status)


if __name__ == "__main__":
    unittest.main()
