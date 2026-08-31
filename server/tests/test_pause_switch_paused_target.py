import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.task_execution_service import (
    TaskExecutionInvalidError, start_task_execution,
)
from app.services.task_pause_solver_service import replan_pause_switch
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

    def test_replan_preserves_both_source_and_target(self):
        """接替任务自己是"已暂停"时也必须进保留名单。

        落地环节会把状态是运行中／已暂停／已中断、又不在名单里的任务整个跳过，
        一个时间槽都不落。名单里只有源任务时，接替任务原有的时间槽被作废却没有
        替代，剩余工时在时间轴上凭空消失，而排程仍然报成功。
        """
        captured = {}

        def fake_replan(db, task_ids, switch_time, project_id, **kwargs):
            captured.update(kwargs)
            return {"status": "ok"}

        with patch(
            "app.services.task_pause_solver_service.replan_resource_closure",
            side_effect=fake_replan,
        ):
            switch_time = replan_pause_switch(self.db, self.source, self.target, self.now)

        self.assertEqual({self.source.task_id, self.target.task_id},
                         captured["preserved_status_task_ids"])
        self.assertEqual(self.now, switch_time)

    def test_resume_at_the_switch_moment_can_still_use_the_anchor(self):
        """恢复要以切换那一刻为准，而不是重排结束后的当前时间。

        切换会把接替任务的时间槽压成 plan_start == plan_end == 切换时刻的锚点，
        中间的重排求解要跑几秒；晚几十秒回头看，锚点已经"过期"，接替一个已暂停
        的任务就会被判成没有可恢复的未来时间槽。
        """
        self.target.plan_start = self.target.plan_end = self.now
        self.source.actual_end = self.now
        self.db.query(Task).filter(Task.id == 1).one().status = "paused"
        self.db.add(TimeSlot(id=3, task_id=2, instrument_id=1, status="paused", tier="confirmed",
                             plan_start=self.now, plan_end=self.now + timedelta(hours=4)))
        self.db.commit()

        start_task_execution(
            self.db, self.target.id, operator_id=1, allow_queue_insert=True, started_at=self.now,
        )

        self.assertEqual("running", self.target.status)
        self.assertEqual(self.now, self.target.actual_start)

    def test_guard_still_fires_when_nothing_is_left_to_resume(self):
        """锚点之外确实没有任何未来时间槽时，仍然要拦住而不是静默启动。"""
        self.target.plan_start = self.target.plan_end = self.now
        self.source.actual_end = self.now
        self.db.query(Task).filter(Task.id == 1).one().status = "paused"
        self.db.commit()

        with self.assertRaises(TaskExecutionInvalidError):
            start_task_execution(
                self.db, self.target.id, operator_id=1, allow_queue_insert=True,
                started_at=self.now + timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main()
