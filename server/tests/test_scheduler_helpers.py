import unittest
from datetime import datetime
from unittest.mock import patch

from app.services.scheduler_helpers import natural_day_boundary, time_horizon, to_units


class SchedulerHelpersTest(unittest.TestCase):
    def test_one_freeze_day_ends_at_today_midnight(self):
        now = datetime(2026, 7, 13, 13, 25, 40)

        boundary = natural_day_boundary(now, 1)

        self.assertEqual(datetime(2026, 7, 13, 23, 59, 59, 999999), boundary)

    def test_two_freeze_days_end_at_tomorrow_midnight(self):
        now = datetime(2026, 7, 13, 23, 50)

        boundary = natural_day_boundary(now, 2)

        self.assertEqual(datetime(2026, 7, 14, 23, 59, 59, 999999), boundary)

    def test_zero_freeze_days_uses_current_time(self):
        now = datetime(2026, 7, 13, 13, 25, 40)

        self.assertEqual(now, natural_day_boundary(now, 0))

    def test_fractional_hours_round_up_to_full_time_units(self):
        self.assertEqual(1, to_units(0.25))
        self.assertEqual(1, to_units(0.5))
        self.assertEqual(2, to_units(0.75))
        self.assertEqual(3, to_units(1.25))

    def test_time_horizon_rounding_crosses_midnight_safely(self):
        class LateNightDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 8, 3, 23, 45, 20)

        with patch(
            "app.services.scheduler_helpers.datetime",
            LateNightDateTime,
        ):
            horizon_start, _, _ = time_horizon()

        self.assertEqual(datetime(2026, 8, 4, 0, 0), horizon_start)

    def test_time_horizon_rounds_up_to_next_half_hour(self):
        class MiddayDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 8, 3, 10, 45, 20)

        with patch(
            "app.services.scheduler_helpers.datetime",
            MiddayDateTime,
        ):
            horizon_start, _, _ = time_horizon()

        self.assertEqual(datetime(2026, 8, 3, 11, 0), horizon_start)

    def test_time_horizon_keeps_aligned_time(self):
        class AlignedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 8, 3, 10, 30)

        with patch(
            "app.services.scheduler_helpers.datetime",
            AlignedDateTime,
        ):
            horizon_start, _, _ = time_horizon()

        self.assertEqual(datetime(2026, 8, 3, 10, 30), horizon_start)

    def test_time_horizon_never_precedes_requested_timestamp(self):
        cases = [
            (datetime(2026, 9, 9, 10, 0, 20, 464087), datetime(2026, 9, 9, 10, 30)),
            (datetime(2026, 9, 9, 10, 30, 0, 1), datetime(2026, 9, 9, 11)),
            (datetime(2026, 9, 9, 23, 30, 1), datetime(2026, 9, 10)),
        ]
        for requested, expected in cases:
            for argument in ("start_at", "now"):
                with self.subTest(requested=requested, argument=argument):
                    start, _, _ = time_horizon(**{argument: requested})
                    self.assertEqual(expected, start)
                    self.assertGreaterEqual(start, requested)

if __name__ == "__main__":
    unittest.main()
