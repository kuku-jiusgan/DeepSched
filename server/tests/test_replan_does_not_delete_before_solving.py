"""重排时，求解之前不许动库里的时间槽。

这是整套改造针对的病根：应用层原先通过数据库跟求解器通信——先把可移动的时间槽
DELETE 掉再求解，求解器回头查库才知道剩下什么。于是"一个假设"的最小单元成了一个
事务而不是一个值：探测只能靠 savepoint 包住再回滚，回滚拦不住已发出的通知；两个
探测并发会撞行锁；而且求解一旦失败，库已经被改过了。

现在"哪些槽让位"是一个槽号集合：求解时把它们排除掉，作废作为写回阶段的一条指令
执行。这条测试在求解那一刻给库拍张照，确认那时旧槽还原封不动。
"""

import unittest
from datetime import datetime, timedelta

from ortools.sat.python import cp_model
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot


class ReplanDoesNotDeleteBeforeSolvingTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        start = datetime.now().replace(hour=8, minute=30, second=0, microsecond=0)
        instrument = Instrument(
            code="REPLAN-1", name="重排仪器",
            availability_status="available", status="idle",
        )
        self.db.add(instrument)
        self.db.flush()
        project = Project(
            code="REPLAN-1", name="重排项目", priority=1,
            start_date=start, end_date=start + timedelta(days=30),
        )
        self.db.add(project)
        self.db.flush()
        self.task = Task(
            project_id=project.id, name="重排任务", task_type="test",
            status="pending", est_duration_hours=2, schedule_dirty=True,
            requires_instrument=True, requires_human=False,
            instrument_ids=[instrument.id],
        )
        self.db.add(self.task)
        self.db.flush()
        # 一条既有的、可移动的时间槽——正是"该让位"的那种。
        self.old_slot = TimeSlot(
            task_id=self.task.id, schedule_run_id="run-0",
            instrument_id=instrument.id,
            plan_start=start + timedelta(days=5),
            plan_end=start + timedelta(days=5, hours=2),
            tier="confirmed", status="scheduled", lifecycle_status="active",
        )
        self.db.add(self.old_slot)
        self.project_id = project.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_the_old_slot_is_still_intact_when_the_solver_runs(self):
        from app.services.project_plan_apply_service import apply_project_plan

        seen = {}
        real_solve = cp_model.CpSolver.Solve

        def _solve(solver, model, *args, **kwargs):
            # 求解这一刻给库拍照：旧槽必须还在，而且没被作废。
            slot = self.db.query(TimeSlot).filter(
                TimeSlot.id == self.old_slot.id,
            ).one()
            seen.setdefault("at_solve", (slot.lifecycle_status, slot.status))
            return real_solve(solver, model, *args, **kwargs)

        cp_model.CpSolver.Solve = _solve
        try:
            result = apply_project_plan(self.db, self.project_id)
        finally:
            cp_model.CpSolver.Solve = real_solve

        self.assertEqual("applied", getattr(result, "status", None), result)
        self.assertIn("at_solve", seen, "没有走到求解，这条测试是空转")
        self.assertEqual(
            ("active", "scheduled"), seen["at_solve"],
            "求解之前旧时间槽已经被改动了——又回到了'先删再算'",
        )
        # 求解之后才作废，新槽同时落地。
        self.db.refresh(self.old_slot)
        self.assertEqual("superseded", self.old_slot.lifecycle_status)
        self.assertTrue(
            self.db.query(TimeSlot).filter(
                TimeSlot.lifecycle_status == "active",
                TimeSlot.task_id == self.task.id,
            ).count() >= 1,
            "重排之后应当有新的现行时间槽",
        )


if __name__ == "__main__":
    unittest.main()
