import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.task_dependency_service import create_continuous_successor
from app.services.task_pause_switch_context_service import build_pause_switch_context


class PauseSwitchIntermediateFollowupTest(unittest.TestCase):
    """中间任务的连续后续任务必须跟在它自己的前驱之后。

    闭包按仪器队列圈定，中间任务的后续往往是不占仪器的方案撰写。它们若以自己
    时间槽的先后混在中间队列里，而那个槽还停在上一批次的老位置、时间上早于
    前驱，队列顺序就会被原样当成硬约束交给求解器——方向完全相反，前驱被迫排到
    后续之后，中间空出大段时间。
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
            Project(id=3, code="C", name="项目C"),
            Task(id=10, project_id=3, name="标准计划", task_type="manual", status="pending"),
            Task(id=1, project_id=1, name="方法开发A", task_type="FFKF_001",
                 requires_instrument=True, assignee_id=1, status="running", est_duration_hours=8),
            Task(id=2, project_id=2, name="方法开发B", task_type="FFKF_001",
                 requires_instrument=True, assignee_id=1, status="scheduled", est_duration_hours=4),
            Task(id=3, project_id=3, parent_id=10, name="方法开发C", task_type="FFKF_001",
                 requires_instrument=True, assignee_id=1, status="scheduled", est_duration_hours=6),
            Task(id=4, project_id=3, parent_id=10, name="方案撰写C", task_type="QCFA_001",
                 requires_human=True, assignee_id=1, status="scheduled", est_duration_hours=2),
        ])
        self.db.flush()
        self.db.add(create_continuous_successor(
            self.db.query(Task).get(3), self.db.query(Task).get(4)))
        self.source = TimeSlot(id=1, task_id=1, instrument_id=1, status="running", tier="confirmed",
                               plan_start=self.now - timedelta(hours=2), plan_end=self.now + timedelta(hours=6),
                               actual_start=self.now - timedelta(hours=2))
        self.target = TimeSlot(id=2, task_id=2, instrument_id=1, status="scheduled", tier="confirmed",
                               plan_start=self.now + timedelta(days=1), plan_end=self.now + timedelta(days=1, hours=4))
        # 项目C 的方案撰写停在上一批次的老位置：早于它的前驱方法开发
        self.followup_slot = TimeSlot(id=3, task_id=4, instrument_id=None, status="scheduled", tier="confirmed",
                                      plan_start=self.now + timedelta(days=2), plan_end=self.now + timedelta(days=2, hours=2))
        self.dev_slot = TimeSlot(id=4, task_id=3, instrument_id=1, status="scheduled", tier="confirmed",
                                 plan_start=self.now + timedelta(days=3), plan_end=self.now + timedelta(days=3, hours=6))
        self.db.add_all([self.source, self.target, self.followup_slot, self.dev_slot])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_followup_is_queued_after_its_predecessor(self):
        context = build_pause_switch_context(self.db, self.source, self.target, self.now)

        order = [entry.task.id for entry in context.queue]
        self.assertIn(3, order)
        self.assertIn(4, order)
        self.assertLess(order.index(3), order.index(4))

    def test_no_inverted_dependency_is_emitted(self):
        context = build_pause_switch_context(self.db, self.source, self.target, self.now)

        self.assertNotIn((3, 4), context.queue_dependencies)
        self.assertIn((4, 3), context.queue_dependencies)

    def test_followup_slots_are_replanned_too(self):
        context = build_pause_switch_context(self.db, self.source, self.target, self.now)

        self.assertIn(self.followup_slot, context.replaceable_slots)


if __name__ == "__main__":
    unittest.main()
