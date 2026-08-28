import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.services.scheduler_deadline_recommendation import (
    enumerate_verified_date_adjustments,
)


class SchedulerDateAdjustmentsTest(unittest.TestCase):
    def setUp(self):
        self.deadline = datetime(2026, 9, 1, 23, 59)
        self.originals = {1: self.deadline, 2: self.deadline}
        self.horizon_end = self.deadline + timedelta(days=4)
        self.labels = {1: "测试项目B", 2: "测试项目A"}

    def enumerate_with(self, validator):
        with patch(
            "app.services.scheduler_deadline_recommendation._validate_deadlines",
            side_effect=validator,
        ):
            return enumerate_verified_date_adjustments(
                object(), object(), [1, 2], self.originals, self.horizon_end, {}, self.labels,
            )

    def test_returns_each_independent_project_adjustment(self):
        results = self.enumerate_with(
            lambda _db, _scheduler, changes, _kwargs: (
                len(changes) == 1
                and next(iter(changes.values())).date() >= self.deadline.date() + timedelta(days=2)
            )
        )

        self.assertEqual([[1], [2]], [result["projects"] for result in results])
        self.assertTrue(all(result["verified"] for result in results))
        self.assertEqual(2, results[0]["changes"][0]["delay_days"])

    def test_returns_combination_when_single_adjustment_cannot_succeed(self):
        results = self.enumerate_with(
            lambda _db, _scheduler, changes, _kwargs: (
                set(changes) == {1, 2}
                and sum((date.date() - self.deadline.date()).days for date in changes.values()) >= 3
            )
        )

        self.assertEqual(1, len(results))
        self.assertEqual([1, 2], results[0]["projects"])
        self.assertEqual(3, sum(change["delay_days"] for change in results[0]["changes"]))

    def test_excludes_superset_of_successful_single_adjustment(self):
        results = self.enumerate_with(
            lambda _db, _scheduler, changes, _kwargs: 1 in changes,
        )

        self.assertEqual([[1]], [result["projects"] for result in results])

    def test_returns_empty_when_horizon_contains_no_solution(self):
        results = self.enumerate_with(
            lambda _db, _scheduler, _changes, _kwargs: False,
        )

        self.assertEqual([], results)


if __name__ == "__main__":
    unittest.main()
