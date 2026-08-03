import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.stats import lab_status
from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User


class LabStatusNextTaskTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_next_task_includes_project_and_assignee(self):
        now = datetime.now()
        user = User(id=1, username="analyst", display_name="张分析", role="分析员")
        project = Project(id=1, name="杂质研究项目", code="XM-001", manager_id=1)
        instrument = Instrument(id=1, code="LCMS-01", name="液质联用仪", status="idle")
        task = Task(
            id=1,
            project_id=1,
            name="方法验证",
            task_type="FFYZ_001",
            assignee_id=1,
            status="scheduled",
        )
        slot = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=now + timedelta(hours=1),
            plan_end=now + timedelta(hours=5),
            status="scheduled",
        )
        self.db.add_all([user, project, instrument, task, slot])
        self.db.commit()

        item = lab_status(self.db)[0]

        self.assertEqual("方法验证", item["next_task"])
        self.assertEqual("杂质研究项目", item["next_project"])
        self.assertEqual("XM-001", item["next_project_code"])
        self.assertEqual("张分析", item["next_user"])

    def test_overdue_scheduled_task_remains_idle_before_manual_start(self):
        now = datetime.now()
        user = User(id=1, username="analyst", display_name="江秀秀", role="分析员")
        project = Project(id=1, name="克拉霉素项目", code="XM2026194", manager_id=1)
        instrument = Instrument(id=1, code="ZBYY-002-0002", name="三重四极气质联用仪", status="idle")
        method = Task(id=1, project_id=1, name="方法开发", task_type="FFKF_001", assignee_id=1, status="scheduled")
        validation = Task(id=2, project_id=1, name="方法验证", task_type="FFYZ_001", assignee_id=1, status="scheduled")
        first_start = now - timedelta(hours=2)
        self.db.add_all([
            user,
            project,
            instrument,
            method,
            validation,
            TimeSlot(task_id=1, instrument_id=1, plan_start=first_start, plan_end=now - timedelta(hours=1), status="scheduled"),
            TimeSlot(task_id=1, instrument_id=1, plan_start=now + timedelta(hours=12), plan_end=now + timedelta(hours=20), status="scheduled"),
            TimeSlot(task_id=2, instrument_id=1, plan_start=now + timedelta(days=2), plan_end=now + timedelta(days=3), status="scheduled"),
        ])
        self.db.commit()

        item = lab_status(self.db)[0]

        self.assertIsNone(item["current_task"])
        self.assertIsNone(item["running_start"])
        self.assertEqual("idle", item["status"])
        self.assertEqual("方法开发", item["next_task"])

    def test_blocked_task_without_actual_start_is_not_current(self):
        now = datetime.now()
        project = Project(id=1, name="项目", code="XM-002")
        instrument = Instrument(id=1, code="LCMS-01", name="液质联用仪", status="idle")
        previous = Task(id=1, project_id=1, name="上一任务", task_type="FFKF_001", status="blocked")
        next_task = Task(id=2, project_id=1, name="下一任务", task_type="FFYZ_001", status="scheduled")
        self.db.add_all([
            project,
            instrument,
            previous,
            next_task,
            TimeSlot(task_id=1, instrument_id=1, plan_start=now - timedelta(hours=2), plan_end=now - timedelta(hours=1), status="blocked"),
            TimeSlot(task_id=2, instrument_id=1, plan_start=now + timedelta(hours=1), plan_end=now + timedelta(hours=2), status="scheduled"),
        ])
        self.db.commit()

        item = lab_status(self.db)[0]

        self.assertIsNone(item["current_task"])
        self.assertEqual("idle", item["status"])
        self.assertEqual("下一任务", item["next_task"])

    def test_actually_started_next_task_replaces_previous_task(self):
        now = datetime.now()
        project = Project(id=1, name="项目", code="XM-003")
        instrument = Instrument(id=1, code="LCMS-01", name="液质联用仪", status="running")
        previous = Task(id=1, project_id=1, name="上一任务", task_type="FFKF_001", status="blocked")
        next_task = Task(id=2, project_id=1, name="下一任务", task_type="FFYZ_001", status="running")
        next_plan_start = now - timedelta(minutes=30)
        self.db.add_all([
            project,
            instrument,
            previous,
            next_task,
            TimeSlot(task_id=1, instrument_id=1, plan_start=now - timedelta(hours=2), plan_end=now - timedelta(hours=1), status="blocked"),
            TimeSlot(
                task_id=2,
                instrument_id=1,
                plan_start=next_plan_start,
                plan_end=now + timedelta(hours=1),
                actual_start=now - timedelta(minutes=5),
                status="running",
            ),
        ])
        self.db.commit()

        item = lab_status(self.db)[0]

        self.assertEqual("下一任务", item["current_task"])
        self.assertEqual(next_plan_start.isoformat(), item["running_start"])

    def test_completed_task_clears_stale_running_instrument_status(self):
        now = datetime.now()
        project = Project(id=1, name="项目", code="XM-004")
        instrument = Instrument(id=1, code="ZBYY-002-0001", name="液质联用仪", status="running")
        task = Task(id=1, project_id=1, name="已完成检测", task_type="test", status="done")
        slot = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=now - timedelta(hours=2),
            plan_end=now - timedelta(hours=1),
            status="completed",
        )
        self.db.add_all([project, instrument, task, slot])
        self.db.commit()

        item = lab_status(self.db)[0]

        self.assertIsNone(item["current_task"])
        self.assertEqual("idle", item["status"])
        self.assertEqual("idle", self.db.get(Instrument, instrument.id).status)


if __name__ == "__main__":
    unittest.main()
