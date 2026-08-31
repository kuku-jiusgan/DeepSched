import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.task_pause_switch_context_service import build_pause_switch_context


class PauseSwitchPausedTargetTest(unittest.TestCase):
    """切换到一个此前已被暂停的任务时，保留名单必须仍然指向源任务。

    候选列表允许选择已暂停的任务（界面上带「恢复」标签）。此前
    paused_source_task_id 是靠 next(entry.status == "paused") 从队列里猜的，
    而队列第一个元素是目标任务、状态取自目标时间槽——目标自己是 paused 时就会
    猜错。落地环节据此跳过真正的源任务，它的剩余工时被静默丢弃：排程报成功，
    任务却一个时间槽都没有。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.now = datetime(2026, 9, 1, 10, 0)
        self.db.add_all([
            User(id=1, username="op", display_name="操作员", role="技术员"),
            Instrument(id=1, code="CSYQ", name="测试仪器"),
            Project(id=1, code="A", name="项目A"), Project(id=2, code="B", name="项目B"),
            Task(id=1, project_id=1, name="方法开发A", task_type="FFKF_001",
                 requires_instrument=True, assignee_id=1, status="running", est_duration_hours=8),
            Task(id=2, project_id=2, name="方法开发B", task_type="FFKF_001",
                 requires_instrument=True, assignee_id=1, status="paused", est_duration_hours=4),
        ])
        self.source = TimeSlot(id=1, task_id=1, instrument_id=1, status="running", tier="confirmed",
                               plan_start=self.now - timedelta(hours=2), plan_end=self.now + timedelta(hours=6),
                               actual_start=self.now - timedelta(hours=2))
        # 目标本身处于暂停状态——也就是界面上带「恢复」标签的候选
        self.target = TimeSlot(id=2, task_id=2, instrument_id=1, status="paused", tier="confirmed",
                               plan_start=self.now + timedelta(days=1), plan_end=self.now + timedelta(days=1, hours=4))
        self.db.add_all([self.source, self.target])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_paused_source_is_the_source_not_the_paused_target(self):
        context = build_pause_switch_context(self.db, self.source, self.target, self.now)

        self.assertEqual(self.source.task_id, context.paused_source_task_id)
        self.assertNotEqual(self.target.task_id, context.paused_source_task_id)

    def test_still_correct_when_target_is_merely_scheduled(self):
        self.target.status = "scheduled"
        self.db.query(Task).filter(Task.id == 2).one().status = "scheduled"
        self.db.commit()

        context = build_pause_switch_context(self.db, self.source, self.target, self.now)

        self.assertEqual(self.source.task_id, context.paused_source_task_id)


if __name__ == "__main__":
    unittest.main()
