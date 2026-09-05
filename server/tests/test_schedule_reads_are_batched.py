import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TimeSlot


class MovableCandidateReadsAreBatchedTest(unittest.TestCase):
    """筛选可移动候选任务时，SQL 条数不能随候选数量增长。

    这几处判定原先都是在循环里逐个任务查——"这个项目开工了吗"、"这个任务有
    不可移动的槽吗"、"这个任务还有以后的槽吗"各一条。一次排程光这一个函数就发
    了 40 多条 SQL，而排程本身要跑几十次（交期建议一次搜索就有 5 次完整排程）。
    判定规则没有变，变的只是问法：一次问清楚整批，而不是一个一个问。

    这里用"候选任务翻三倍，SQL 条数不变"来钉住这个性质——数绝对条数会随实现
    细节漂移，数增长才是真正要守住的东西。
    """

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.statements = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(statement)

    def tearDown(self):
        self.db.close()

    def _make_candidates(self, count: int) -> None:
        low = Project(code="LOW-%d" % count, name="低优先级项目", priority=9,
                      end_date=datetime.now() + timedelta(days=60))
        self.db.add(low)
        self.db.flush()
        for index in range(count):
            task = Task(project_id=low.id, name="任务%d" % index, task_type="test",
                        status="scheduled", est_duration_hours=1,
                        requires_instrument=True, requires_human=False)
            self.db.add(task)
            self.db.flush()
            self.db.add(TimeSlot(
                task_id=task.id, instrument_id=1, lifecycle_status="active",
                tier="confirmed", status="scheduled",
                plan_start=datetime.now() + timedelta(days=1),
                plan_end=datetime.now() + timedelta(days=2),
            ))
        self.db.commit()

    def _count_queries(self, count: int) -> int:
        from app.services.schedule_insert_service import _load_lower_priority_movable_tasks

        self._make_candidates(count)
        self.statements.clear()
        _load_lower_priority_movable_tasks(
            self.db, insert_priority=1, excluded_task_ids=set(),
            selected_instrument_ids={1}, unstarted_projects_only=True,
        )
        return len(self.statements)

    def test_query_count_does_not_grow_with_the_number_of_candidates(self):
        few = self._count_queries(3)
        many = self._count_queries(12)

        self.assertEqual(few, many, "候选从 3 个涨到 15 个，SQL 从 %d 条涨到 %d 条" % (few, many))


class ProtectedSlotLookupIsBatchedTest(unittest.TestCase):
    """判断"哪些任务有不可移动的时间槽"要一次问清楚，不能一个任务一条 SQL。"""

    def test_one_query_regardless_of_task_count(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        statements = []

        @event.listens_for(engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        try:
            from app.services.schedule_slot_protection_service import (
                tasks_with_immovable_slot,
            )

            statements.clear()
            tasks_with_immovable_slot(db, range(1, 31))
            self.assertEqual(1, len(statements), statements)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()


class SolverTraceReadsAreBatchedTest(unittest.TestCase):
    """求解日志的取数不能随时间槽数量增长。

    这份日志是排查求解结果用的，本身不影响排程。但它原先是逐槽懒加载——每条槽各查
    一次所属项目、负责人、仪器，再顺着父任务上溯。实测一次真实排程光写这份日志就
    发 27 条 SQL，和整个装载阶段（29 条）几乎一样多。
    """

    def _fixture(self, slot_count: int):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        # commit 之后实体会过期，之后每次读属性都要回查一次——那是 ORM 的行为，
        # 会把"取数条数随槽数增长"这件事伪造出来。这里关掉它，测的才是取数本身。
        db = sessionmaker(bind=engine, expire_on_commit=False)()
        now = datetime.now().replace(second=0, microsecond=0)
        project = Project(code="TRACE-1", name="日志项目", priority=1,
                          start_date=now, end_date=now + timedelta(days=30))
        db.add(project)
        db.flush()
        parent = Task(project_id=project.id, name="顶层任务", task_type="test",
                      status="pending", est_duration_hours=1,
                      requires_instrument=False, requires_human=False)
        db.add(parent)
        db.flush()
        slots = []
        for index in range(slot_count):
            task = Task(project_id=project.id, name="子任务%d" % index,
                        task_type="test", status="scheduled", est_duration_hours=1,
                        parent_id=parent.id,
                        requires_instrument=False, requires_human=False)
            db.add(task)
            db.flush()
            slot = TimeSlot(
                task_id=task.id, schedule_run_id="run-0", instrument_id=None,
                plan_start=now + timedelta(hours=index),
                plan_end=now + timedelta(hours=index + 1),
                tier="confirmed", status="scheduled", lifecycle_status="active",
            )
            db.add(slot)
            db.flush()
            slots.append(slot)
        db.commit()
        return engine, db, slots

    def _count(self, slot_count: int) -> int:
        from app.services.scheduler_solver_trace_service import _resolve_slot_names

        engine, db, slots = self._fixture(slot_count)
        statements = []

        @event.listens_for(engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        try:
            names = _resolve_slot_names(db, slots)
            # 顺带确认名字真的解析出来了，否则条数为零也"通过"。
            self.assertEqual("TRACE-1", names[slots[0].id]["project"])
            self.assertEqual("顶层任务", names[slots[0].id]["top_task"])
            return len(statements)
        finally:
            db.close()

    def test_query_count_does_not_grow_with_the_number_of_slots(self):
        few = self._count(2)
        many = self._count(12)

        self.assertEqual(few, many, "槽从 2 条涨到 12 条，SQL 从 %d 条涨到 %d 条" % (few, many))
