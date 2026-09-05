"""时间槽变更日志必须能指回它记录的那个槽，并保留变更前的真实状态。

这份日志是排查"这个槽为什么挪了/没了"的唯一依据。两处缺陷让它长期失真：
新建记录在拿到主键之前就写了，槽号全是空；作废记录先把状态收成 cancelled
再读它，"变更前状态"于是永远是 cancelled。修复前线上 1878 条新建记录里 1852 条
没有槽号，1820 条作废记录里 651 条的变更前状态是 cancelled。
"""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, ScheduleSlotChangeLog, Task, TimeSlot


class SlotChangeLogFidelityTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        now = datetime.now().replace(second=0, microsecond=0)
        project = Project(code="LOG-1", name="日志项目", priority=1,
                          start_date=now, end_date=now + timedelta(days=10))
        self.db.add(project)
        self.db.flush()
        self.task = Task(project_id=project.id, name="日志任务", task_type="test",
                         status="pending", est_duration_hours=1,
                         requires_instrument=False, requires_human=False)
        self.db.add(self.task)
        self.db.flush()
        self.now = now

    def tearDown(self):
        self.db.close()

    def test_created_log_carries_the_slot_id(self):
        from app.services.scheduler_persistence import _create_slot

        _create_slot(
            self.db, self.task, None, self.now, self.now + timedelta(hours=1),
            self.now - timedelta(days=1), self.now + timedelta(days=1), "run-1",
        )
        self.db.flush()

        log = self.db.query(ScheduleSlotChangeLog).filter(
            ScheduleSlotChangeLog.change_type == "created",
        ).one()
        slot = self.db.query(TimeSlot).one()
        self.assertEqual(slot.id, log.slot_id)

    def test_superseded_log_keeps_the_status_from_before_the_change(self):
        from app.services.schedule_slot_change_log_service import supersede_slot

        slot = TimeSlot(
            task_id=self.task.id, schedule_run_id="run-1", instrument_id=None,
            plan_start=self.now, plan_end=self.now + timedelta(hours=1),
            tier="confirmed", status="paused", lifecycle_status="active",
        )
        self.db.add(slot)
        self.db.flush()

        supersede_slot(self.db, slot, "排程重排")
        self.db.flush()

        log = self.db.query(ScheduleSlotChangeLog).filter(
            ScheduleSlotChangeLog.change_type == "superseded",
        ).one()
        self.assertEqual("paused", log.before_status)
        self.assertEqual("cancelled", slot.status)


if __name__ == "__main__":
    unittest.main()
