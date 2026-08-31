import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.schedule_working_time_service import (
    advance_working_hours,
    working_hours_between,
    working_time_spans,
)


class ScheduleWorkingTimeServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_spans_skip_the_weekend(self):
        """周五开始、周一才结束的窗口必须拆成两段，周末不出现。"""
        spans = working_time_spans(
            self.db, datetime(2026, 8, 28, 11, 10), datetime(2026, 8, 31, 8, 52),
        )

        self.assertEqual(
            [
                (datetime(2026, 8, 28, 11, 10), datetime(2026, 8, 28, 20, 0)),
                (datetime(2026, 8, 31, 8, 30), datetime(2026, 8, 31, 8, 52)),
            ],
            spans,
        )

    def test_spans_skip_the_nightly_gap(self):
        spans = working_time_spans(
            self.db, datetime(2026, 8, 26, 17, 24), datetime(2026, 8, 27, 10, 43),
        )

        self.assertEqual(
            [
                (datetime(2026, 8, 26, 17, 24), datetime(2026, 8, 26, 20, 0)),
                (datetime(2026, 8, 27, 8, 30), datetime(2026, 8, 27, 10, 43)),
            ],
            spans,
        )

    def test_spans_total_equals_working_hours_between(self):
        start, end = datetime(2026, 8, 28, 11, 10), datetime(2026, 9, 2, 15, 0)
        spans = working_time_spans(self.db, start, end)

        total = sum((b - a).total_seconds() for a, b in spans) / 3600
        self.assertAlmostEqual(working_hours_between(self.db, start, end), total, places=4)

    def test_window_entirely_outside_working_hours_yields_no_spans(self):
        self.assertEqual([], working_time_spans(
            self.db, datetime(2026, 8, 29, 9, 0), datetime(2026, 8, 30, 18, 0),
        ))

    def test_zero_or_negative_hours_returns_start(self):
        start = datetime(2026, 9, 1, 9, 0)
        self.assertEqual(start, advance_working_hours(self.db, start, 0))
        self.assertEqual(start, advance_working_hours(self.db, start, -3))

    def test_advance_is_inverse_of_working_hours_between(self):
        start = datetime(2026, 9, 1, 9, 0)
        for hours in (1.5, 6, 17, 40):
            end = advance_working_hours(self.db, start, hours)
            self.assertAlmostEqual(
                hours, working_hours_between(self.db, start, end), places=4,
                msg=f"hours={hours}",
            )

    def test_advance_skips_non_working_days(self):
        # 2026-09-04 是周五，跨过周末后应落在下一个工作日。
        friday = datetime(2026, 9, 4, 9, 0)
        end = advance_working_hours(self.db, friday, 16)
        self.assertGreater(end, friday + timedelta(days=2))
        self.assertNotIn(end.weekday(), (5, 6))

    def test_start_before_working_window_waits_for_window_open(self):
        before_open = datetime(2026, 9, 1, 3, 0)
        end = advance_working_hours(self.db, before_open, 1)
        self.assertAlmostEqual(
            1.0, working_hours_between(self.db, before_open, end), places=4,
        )


if __name__ == "__main__":
    unittest.main()
