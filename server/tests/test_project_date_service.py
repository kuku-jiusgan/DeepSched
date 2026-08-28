import unittest
from datetime import datetime

from app.services.project_date_service import (
    normalize_project_end,
    normalize_project_start,
)


class ProjectDateServiceTest(unittest.TestCase):
    def test_end_date_normalizes_to_last_second_of_the_chosen_day(self):
        normalized = normalize_project_end(datetime(2026, 9, 9, 0, 0))

        self.assertEqual(datetime(2026, 9, 9, 23, 59, 59), normalized)

    def test_end_date_carries_no_fractional_seconds(self):
        # end_date 是秒精度的 DATETIME 列，MySQL 默认对小数秒进位，
        # 23:59:59.999999 会被存成次日 00:00:00，界面上选「9 月 9 日」
        # 就会显示成「9-10 00:00」，让人误以为截止日期是 10 号。
        normalized = normalize_project_end(datetime(2026, 9, 9, 8, 30))

        self.assertEqual(0, normalized.microsecond)
        self.assertEqual(9, normalized.day)

    def test_start_date_normalizes_to_midnight(self):
        self.assertEqual(
            datetime(2026, 9, 9, 0, 0),
            normalize_project_start(datetime(2026, 9, 9, 15, 20)),
        )

    def test_none_passes_through(self):
        self.assertIsNone(normalize_project_end(None))
        self.assertIsNone(normalize_project_start(None))


if __name__ == "__main__":
    unittest.main()
