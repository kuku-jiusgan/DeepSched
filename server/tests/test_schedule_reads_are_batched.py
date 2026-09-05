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
