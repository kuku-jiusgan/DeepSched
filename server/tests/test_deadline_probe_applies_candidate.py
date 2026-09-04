import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task
from app.services.scheduler import SchedulerService
from app.services.scheduler_deadline_recommendation import (
    FEASIBLE,
    INFEASIBLE,
    _probe_deadlines,
)


class DeadlineProbeAppliesCandidateTest(unittest.TestCase):
    """候选结题日必须真的进模型。

    方案搜索的每个候选都靠一次试解判可行与否。如果候选日期没有真正作用到模型上，
    每次试解看到的都是原结题日，于是"延到年底也排不下"这种不可能的结论就会成批
    出现——而它长得和真正的不可行一模一样，从结果上分辨不出来。所以要有一个
    "近了排不下、远了排得下"的最小场景把这条通路钉住。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        # 求解窗口收在下一个整周的周一到周五：模型足够小，5 秒内能给出确定结论，
        # 也避开周末导致的可用工时抖动。
        today = datetime.now().date()
        monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        self.start = datetime.combine(monday, datetime.min.time()).replace(hour=8, minute=30)
        self.near = self.start.replace(hour=23, minute=59) + timedelta(days=1)   # 周二
        self.far = self.start.replace(hour=23, minute=59) + timedelta(days=3)    # 周四
        # 前置校验要求至少有一台可用仪器，否则连模型都不会建。
        self.db.add(Instrument(
            code="PROBE-INST", name="探测仪器", availability_status="available",
            status="idle",
        ))
        self.project = Project(
            code="PROBE-1", name="候选日期探测", priority=3, end_date=self.near,
        )
        self.db.add(self.project)
        self.db.flush()
        # 每个工作日 11.5 小时。30 小时的活：周一+周二 23 小时装不下，
        # 做到周四 46 小时装得下。
        self.task = Task(
            project_id=self.project.id, name="方法开发", task_type="test",
            status="pending", est_duration_hours=30,
            requires_instrument=False, requires_human=False,
        )
        self.db.add(self.task)
        self.db.commit()
        self.kwargs = {
            "current_project_id": self.project.id,
            "task_ids": [self.task.id],
            "planning_start_at": self.start,
            "planning_end_at": self.start + timedelta(days=4, hours=11, minutes=30),
        }

    def tearDown(self):
        self.db.close()

    def _probe(self, deadline):
        return _probe_deadlines(
            self.db, SchedulerService(self.db), {self.project.id: deadline}, self.kwargs,
        )

    def test_candidate_deadline_changes_the_verdict(self):
        near_verdict = self._probe(self.near)
        far_verdict = self._probe(self.far)

        # 近的排不下（5 秒内不一定证得完，但绝不能是可行）、远的排得下。
        # 两个判定必须不同——相同就说明候选日期压根没作用到模型上。
        self.assertNotEqual(FEASIBLE, near_verdict)
        self.assertEqual(FEASIBLE, far_verdict)
        self.assertIn(near_verdict, {INFEASIBLE, "undetermined"})

    def test_probe_does_not_leave_the_candidate_behind(self):
        self._probe(self.far)
        self.db.refresh(self.project)
        self.assertEqual(self.near, self.project.end_date)
