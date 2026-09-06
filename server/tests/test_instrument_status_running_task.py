import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.instrument_status_service import (
    effective_instrument_status, instrument_is_working,
)
from app.services.lab_status_service import list_lab_status


class InstrumentShowsRunningWhileTaskIsRunningTest(unittest.TestCase):
    """只要这台仪器上还有仪器任务在进行中，两个界面都要显示运作中。

    早先的判定是"有没有一个实际开始了、还没结束的时间槽"。它漏掉了跨时段的空档：
    线上 XM2026224 · 方法开发 9 月 4 日开工，当天那段按计划边界 18:00 收了尾，剩下
    三段排在 9 月 7、8、9 日，任务状态一直是进行中——中间这两天首页和仪器甘特图都
    显示 ZBYY-002-0006 空闲，而任务并没有做完。

    这个口径只管状态展示：排程判定和各类统计口径都不受影响。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(username="tech", display_name="李伟", role="技术员", is_active=True)
        self.instrument = Instrument(
            code="ZBYY-002-0006", name="电感耦合等离子体质谱仪",
            status="idle", availability_status="available",
        )
        self.project = Project(code="XM2026224", name="元素杂质方法开发", project_kind="project")
        self.db.add_all([self.user, self.instrument, self.project])
        self.db.flush()
        self.task = Task(
            project_id=self.project.id, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, requires_human=True, assignee_id=self.user.id,
            status="running", est_duration_hours=16,
        )
        self.db.add(self.task)
        self.db.flush()
        # 已经做过的那一段：实际开始也实际结束了
        self.db.add(TimeSlot(
            task_id=self.task.id, instrument_id=self.instrument.id,
            plan_start=datetime(2026, 9, 4, 14, 0), plan_end=datetime(2026, 9, 4, 18, 0),
            actual_start=datetime(2026, 9, 4, 13, 56), actual_end=datetime(2026, 9, 4, 18, 0),
            status="completed", tier="confirmed",
        ))
        # 还没到的那几段
        for day in (7, 8):
            self.db.add(TimeSlot(
                task_id=self.task.id, instrument_id=self.instrument.id,
                plan_start=datetime(2026, 9, day, 8, 30),
                plan_end=datetime(2026, 9, day, 18, 0),
                status="scheduled", tier="confirmed",
            ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_instrument_is_running_between_two_scheduled_segments(self):
        self.assertTrue(instrument_is_working(self.db, self.instrument.id))
        self.assertEqual("running", effective_instrument_status(self.db, self.instrument))

    def test_lab_status_agrees_and_still_names_the_current_task(self):
        """状态说运作中，"当前任务"就不能是空的，否则界面自相矛盾。"""
        item = next(row for row in list_lab_status(self.db) if row["id"] == self.instrument.id)

        self.assertEqual("running", item["status"])
        self.assertEqual("方法开发", item["current_task"])
        self.assertEqual("李伟", item["current_user"])
        # 可以操作的时间槽仍然只认真正在跑的那一段，这里没有，就该是空的。
        self.assertIsNone(item["running_slot_id"])

    def test_task_not_started_yet_leaves_the_instrument_idle(self):
        """只放宽"进行中"，没开工的活不能把仪器点亮。"""
        self.task.status = "scheduled"
        self.db.commit()

        self.assertFalse(instrument_is_working(self.db, self.instrument.id))
        self.assertEqual("idle", effective_instrument_status(self.db, self.instrument))

    def test_paused_task_does_not_count_as_running(self):
        """暂停不是运行中。"""
        self.task.status = "paused"
        self.db.commit()

        self.assertFalse(instrument_is_working(self.db, self.instrument.id))

    def test_fault_still_wins(self):
        self.instrument.status = "fault"
        self.db.commit()

        self.assertEqual("fault", effective_instrument_status(self.db, self.instrument))


if __name__ == "__main__":
    unittest.main()
