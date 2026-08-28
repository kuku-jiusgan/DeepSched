import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.schedule_working_time_service import (
    advance_working_hours,
    working_hours_between,
)


class ScheduleWorkingTimeServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

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
