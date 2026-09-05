"""建议的结题日必须落在工作日上。

结题日是跟客户签的合同日期，落在周末或法定假日上没有意义；更实际的问题是它腾不出
任何工时——排程只在工作时段里落任务，把结题日从周六挪到周日，可用工时一分钟都没多，
"延期 1 天"等于什么都没做。

线上就出过这种建议：原结题日 2026-09-12（周六），建议延到 09-13（周日）。
"""

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, ScheduleRule, SysCalendar
from app.services.scheduler_deadline_recommendation import _candidate_deadlines


class CandidateDeadlinesAreWorkingDaysTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()
        # 2026-09-12 周六、09-13 周日、09-14 周一
        for day, working, kind in [
            ("2026-09-11", 1, "workday"),
            ("2026-09-12", 0, "weekend"),
            ("2026-09-13", 0, "weekend"),
            ("2026-09-14", 1, "workday"),
            ("2026-09-15", 1, "workday"),
        ]:
            self.db.add(SysCalendar(
                date=datetime.strptime(day, "%Y-%m-%d").date(),
                is_working_day=working, day_type=kind,
            ))
        self.project = Project(code="CAL-1", name="日历项目", priority=1,
                               end_date=datetime(2026, 9, 12, 23, 59))
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _candidates(self):
        return _candidate_deadlines(
            self.db, [self.project.id],
            {self.project.id: self.project.end_date},
            datetime(2026, 9, 15, 23, 59),
        )[self.project.id]

    def test_weekends_are_not_offered(self):
        days = [item.date().isoformat() for item in self._candidates()]

        self.assertNotIn("2026-09-13", days, "周日不该出现在候选结题日里")
        self.assertEqual(["2026-09-14", "2026-09-15"], days)

    def test_weekends_are_offered_when_the_rule_counts_them_as_working_time(self):
        """排程规则若把周末算作工作时间，周末结题日就是合理的。"""
        self.db.add(ScheduleRule(
            code="working_hours", name="工作时间", category="constraint",
            is_enabled=True, params={"include_weekends": True},
        ))
        self.db.commit()

        days = [item.date().isoformat() for item in self._candidates()]

        self.assertIn("2026-09-13", days)


if __name__ == "__main__":
    unittest.main()
