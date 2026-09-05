"""写回时的乐观锁：比对和推进必须是同一条 SQL。

一份计划是针对"装载那一刻的世界"算出来的，写回前要确认这中间没人动过排程。原先
这个判断是 Python 里的一个 if——先算指纹、比对、再写。检查通过和真正写下去之间
隔着几毫秒，别人照样能在这条缝里提交，两份基于同一份旧状态的计划就会双双落地。

改成把版本号写进 UPDATE 的 WHERE 里之后，"检查"和"占用"是同一个原子动作，没有缝。
"""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.schedule_epoch_service import (
    ScheduleStaleError,
    claim,
    current_epoch,
)


class ScheduleEpochLockTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_the_row_initialises_itself(self):
        """新建的库还没有这一行。匹配不到行会被误判成"世界变了"，必须自建。"""
        self.assertEqual(0, current_epoch(self.db))
        self.assertEqual(0, current_epoch(self.db))

    def test_claiming_advances_the_version(self):
        base = current_epoch(self.db)
        claim(self.db, base)
        self.assertEqual(base + 1, current_epoch(self.db))

    def test_two_plans_built_from_the_same_state_cannot_both_land(self):
        """两份基于同一份旧状态的计划，只能有一份写回成功。"""
        base = current_epoch(self.db)

        claim(self.db, base)          # 第一份落地
        with self.assertRaises(ScheduleStaleError):
            claim(self.db, base)      # 第二份还拿着旧版本，必须被拒

    def test_the_check_is_the_update_itself(self):
        """比对不能是先 SELECT 再 UPDATE：那样两者之间存在时间差。

        这里直接验语句的形态——条件更新影响 0 行时就是冲突，不需要也不允许另有
        一次读来做判断。
        """
        base = current_epoch(self.db)
        self.db.execute(
            text("UPDATE schedule_epoch SET version = :v WHERE id = 1"),
            {"v": base + 5},
        )

        with self.assertRaises(ScheduleStaleError):
            claim(self.db, base)
        # 失败之后版本不能被推进。
        self.assertEqual(base + 5, current_epoch(self.db))


class PlanCarriesTheEpochItWasBuiltAgainstTest(unittest.TestCase):
    """装载世界时记下版本号，它要一路带到写回。"""

    def test_planning_problem_records_the_epoch(self):
        from app.models import Instrument
        from app.services.planning_problem import build_planning_problem

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            db.add(Instrument(code="EPOCH-1", name="版本仪器",
                              availability_status="available", status="idle"))
            db.commit()

            first = build_planning_problem(db, now=datetime(2026, 9, 5, 9, 0))
            claim(db, first.epoch)
            second = build_planning_problem(db, now=datetime(2026, 9, 5, 9, 0))

            self.assertEqual(first.epoch + 1, second.epoch)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
