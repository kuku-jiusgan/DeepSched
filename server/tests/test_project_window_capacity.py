"""项目时间窗必须装得下项目工时，保存项目信息时就该拦下。

这是最宽松的判定：假设仪器完全归它独占、没有任何其他项目竞争。连这样都
放不下的项目，无论怎么排都不可能成功，不该拖到排程失败才发现。
"""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.project_hours_validation_service import (
    ProjectWindowCapacityError,
    project_window_capacity_deficit,
    validate_project_window_capacity,
)


class ProjectWindowCapacityTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        # 从下一个周一起算，避免用例受运行当天是周几影响。
        today = datetime.now().date()
        monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        self.start = datetime.combine(monday, datetime.min.time())

    def tearDown(self):
        self.db.close()

    def _deficit(self, hours, days):
        return project_window_capacity_deficit(
            self.db, self.start, self.start + timedelta(days=days), hours,
        )

    def test_five_working_days_hold_about_one_week_of_work(self):
        # 工作日历默认 8:30-20:00，5 个工作日约 57.5 小时。
        self.assertEqual(0, self._deficit(50, 7))
        self.assertGreater(self._deficit(80, 7), 0)

    def test_past_projects_are_out_of_scope(self):
        # 结题日已过时没有工时可排，校验挡住的只是历史数据维护。
        deficit = project_window_capacity_deficit(
            self.db, self.start - timedelta(days=30), datetime.now() - timedelta(days=1), 40,
        )

        self.assertEqual(0, deficit)

    def test_missing_dates_or_hours_are_skipped(self):
        self.assertEqual(0, project_window_capacity_deficit(self.db, None, self.start, 40))
        self.assertEqual(0, project_window_capacity_deficit(self.db, self.start, None, 40))
        self.assertEqual(0, self._deficit(None, 7))

    def test_validation_message_names_the_gap(self):
        with self.assertRaises(ProjectWindowCapacityError) as caught:
            validate_project_window_capacity(
                self.db, self.start, self.start + timedelta(days=7), 200,
            )

        self.assertIn("还差", str(caught.exception))
        self.assertIn("请延长结题日期或下调工时", str(caught.exception))

    def test_existing_violation_may_be_edited_without_worsening(self):
        end = self.start + timedelta(days=7)
        previous = project_window_capacity_deficit(self.db, self.start, end, 200)

        # 缺口不变的保存要放行，否则用户会被卡死在项目页上，连延长结题日都做不了。
        validate_project_window_capacity(
            self.db, self.start, end, 200, previous_deficit=previous,
        )

    def test_worsening_an_existing_violation_is_rejected(self):
        end = self.start + timedelta(days=7)
        previous = project_window_capacity_deficit(self.db, self.start, end, 200)

        with self.assertRaises(ProjectWindowCapacityError):
            validate_project_window_capacity(
                self.db, self.start, end, 260, previous_deficit=previous,
            )


if __name__ == "__main__":
    unittest.main()
