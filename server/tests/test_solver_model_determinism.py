"""同一道题构造两次，求解模型必须逐字节相同。

这是后续把求解输入抽成内存领域模型时唯一的硬门禁：每迁一个模块，都要求模型
序列化后完全不变，从而证明只是换了取数方式、没有改变喂给求解器的那道题。

门禁能成立有两个前提，这条测试同时钉住它们：

1. **时间原点显式传入**。建模过程原本直接读挂钟（时间窗口的原点、运行中时间槽
   被裁到"此刻"的右端），同一道题隔一秒再算就全变了。
2. **构造顺序确定**。CP-SAT 的变量和约束索引按创建顺序分配，所以任何一处遍历
   无序集合、或者查询不带 ORDER BY，都会让模型字节漂移——哪怕逻辑完全等价。

真实语料的对照由 tools/model_equivalence.py 负责（它回放历史排程作业的入参）。
这里用一份最小固定装置，保证这条性质在 CI 里也能被守住。
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class SolverModelIsReproducibleTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.now = datetime(2026, 9, 5, 9, 0, 0)

        # 多台仪器、多个任务才能让"遍历顺序"真正有机会漂移。
        for index in range(3):
            self.db.add(Instrument(
                code="DET-%d" % index, name="检测仪%d" % index,
                availability_status="available", status="idle",
            ))
        project = Project(
            code="DET-1", name="确定性项目", priority=1,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=30),
        )
        self.db.add(project)
        self.db.flush()
        self.task_ids = []
        for index in range(4):
            task = Task(
                project_id=project.id, name="任务%d" % index, task_type="test",
                status="pending", est_duration_hours=2,
                requires_instrument=False, requires_human=False,
            )
            self.db.add(task)
            self.db.flush()
            self.task_ids.append(task.id)
        self.project_id = project.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _capture(self) -> bytes:
        from tools.model_equivalence import capture_model

        captured, early = capture_model(
            self.db,
            {
                "task_ids": sorted(self.task_ids),
                "current_project_id": self.project_id,
                "commit": False,
                "emit_advance_notifications": False,
            },
            now=self.now,
        )
        self.assertIsNotNone(captured, "没能建出模型，提前返回了：%s" % (early,))
        return captured[1]

    def test_building_the_same_problem_twice_gives_identical_bytes(self):
        self.assertEqual(self._capture(), self._capture())

    def test_a_pinned_clock_makes_the_horizon_stable(self):
        """时间原点必须来自传入的 now，不能就地读挂钟。"""
        from app.services.scheduler_helpers import time_horizon

        first = time_horizon(now=self.now)
        second = time_horizon(now=self.now)
        self.assertEqual(first, second)
        self.assertNotEqual(first, time_horizon(now=self.now + timedelta(hours=1)))


class PlanningProblemIsAValueTest(unittest.TestCase):
    """装载出来的求解输入必须是值，不能是绑在会话上的实体。

    实体的问题有三个：属性访问可能悄悄触发一条 SQL（全仓 37 个 ORM 关系都是默认
    延迟加载）；会话一关就不能再用；没法跨进程传。这三条都挡着"把一道题扔给另一
    个进程去算"。仪器已经转成值对象，这里钉住它。
    """

    def test_instruments_survive_the_session_being_closed(self):
        import pickle
        from datetime import datetime as real_datetime

        from app.services.planning_problem import build_planning_problem

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        db.add(Instrument(
            code="VALUE-1", name="值对象仪器",
            availability_status="available", status="idle",
        ))
        db.commit()

        problem = build_planning_problem(db, now=real_datetime(2026, 9, 5, 9, 0, 0))
        db.close()

        # 会话已关闭，实体在这里会抛 DetachedInstanceError。
        self.assertEqual(["VALUE-1"], [item.code for item in problem.instruments])
        self.assertEqual(
            problem.instruments, pickle.loads(pickle.dumps(problem.instruments)),
        )

    def test_the_whole_problem_survives_the_session_and_pickling(self):
        """一道题要能整体序列化、扔给别的进程去算。

        这是"内存领域模型"成不成立的判据：任务、项目、仪器、固定时间槽全部是值，
        会话关掉之后照样读得到。只要还有一处是 ORM 实体，这里就会抛
        DetachedInstanceError 或 pickle 失败。
        """
        import pickle
        from dataclasses import replace
        from datetime import datetime as real_datetime

        from app.models import Project, Task, TimeSlot
        from app.services.planning_problem import (
            build_planning_problem,
            build_task_views,
            to_slot_views,
        )

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine, expire_on_commit=False)()
        now = real_datetime(2026, 9, 7, 9, 0)
        instrument = Instrument(code="WHOLE-1", name="整体仪器",
                                availability_status="available", status="idle")
        db.add(instrument)
        db.flush()
        project = Project(code="WHOLE-1", name="整体项目", priority=1,
                          start_date=now, end_date=now + timedelta(days=30))
        db.add(project)
        db.flush()
        task = Task(project_id=project.id, name="整体任务", task_type="test",
                    status="pending", est_duration_hours=4,
                    requires_instrument=True, requires_human=False,
                    instrument_ids=[instrument.id])
        db.add(task)
        db.flush()
        db.add(TimeSlot(
            task_id=task.id, schedule_run_id="seed", instrument_id=instrument.id,
            plan_start=now + timedelta(days=1), plan_end=now + timedelta(days=1, hours=2),
            tier="confirmed", status="scheduled", lifecycle_status="active",
        ))
        db.commit()

        problem = replace(
            build_planning_problem(db, now=now),
            tasks=tuple(build_task_views(db.query(Task).all())),
        )
        slots = to_slot_views(db.query(TimeSlot).all())
        db.close()

        restored, restored_slots = pickle.loads(pickle.dumps((problem, slots)))

        self.assertEqual("整体任务", restored.tasks[0].name)
        self.assertEqual("WHOLE-1", restored.tasks[0].project.code)
        self.assertEqual("WHOLE-1", restored.instruments[0].code)
        self.assertEqual(project.id, restored_slots[0].task.project_id)


if __name__ == "__main__":
    unittest.main()
