import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.workspace_command_service import complete_workspace_task


class CompleteTaskIdempotencyTest(unittest.TestCase):
    """重复提交「完成任务」不得再次触发重排。

    完成任务会触发资源释放重排，实测二三十秒。期间没有反馈，用户会反复点击。
    早先每个请求各自读到同一份「未完成」的旧快照、各自跑一遍重排、各自落一份
    时间槽——删了一次、插了五次，同一任务留下五份完全重叠的副本，在甘特图上
    严丝合缝叠成一条，看不出来但会让按槽求和的统计翻倍。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        now = datetime(2026, 9, 1, 10, 0)
        self.db.add_all([
            Project(id=1, code="XM-1", name="项目一"),
            Instrument(id=1, code="INST", name="仪器"),
            Task(id=1, project_id=1, name="方法开发", task_type="FFKF_001",
                 requires_instrument=True, status="running", est_duration_hours=4,
                 executed_minutes=240),
        ])
        self.slot = TimeSlot(
            id=1, task_id=1, instrument_id=1, status="running", tier="confirmed",
            plan_start=now - timedelta(hours=2), plan_end=now + timedelta(hours=2),
            actual_start=now - timedelta(hours=2), lifecycle_status="active",
            schedule_run_id="r1",
        )
        self.db.add(self.slot)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_second_completion_is_idempotent(self):
        first = complete_workspace_task(self.db, self.slot.id, release_instrument=False)
        self.db.commit()

        second = complete_workspace_task(self.db, self.slot.id, release_instrument=False)

        self.assertEqual("ok", first["status"])
        self.assertEqual("ok", second["status"])
        self.assertTrue(second.get("duplicate"))

    def test_repeated_completion_creates_no_extra_slots(self):
        complete_workspace_task(self.db, self.slot.id, release_instrument=False)
        self.db.commit()
        before = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == 1, TimeSlot.lifecycle_status == "active",
        ).count()

        for _ in range(4):
            complete_workspace_task(self.db, self.slot.id, release_instrument=False)
        self.db.commit()

        after = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == 1, TimeSlot.lifecycle_status == "active",
        ).count()
        self.assertEqual(before, after)

    def test_second_completion_does_not_move_tasks(self):
        complete_workspace_task(self.db, self.slot.id, release_instrument=False)
        self.db.commit()

        second = complete_workspace_task(self.db, self.slot.id, release_instrument=True)

        self.assertEqual(0, second["moved_tasks"])


if __name__ == "__main__":
    unittest.main()
