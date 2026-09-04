import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.services.scheduler_deadline_recommendation import (
    FEASIBLE,
    INFEASIBLE,
    UNDETERMINED,
    enumerate_verified_date_adjustments,
)


class SchedulerDateAdjustmentsTest(unittest.TestCase):
    def setUp(self):
        self.deadline = datetime(2026, 9, 1, 23, 59)
        self.originals = {1: self.deadline, 2: self.deadline}
        self.horizon_end = self.deadline + timedelta(days=4)
        self.labels = {1: "测试项目B", 2: "测试项目A"}

    def enumerate_with(self, validator):
        """validator 返回真假即可，转成三态判定；要区分超时的用例自己返回判定串。"""
        def probe(db, scheduler, changes, kwargs):
            verdict = validator(db, scheduler, changes, kwargs)
            if verdict in (FEASIBLE, INFEASIBLE, UNDETERMINED):
                return verdict
            return FEASIBLE if verdict else INFEASIBLE

        with patch(
            "app.services.scheduler_deadline_recommendation._probe_deadlines",
            side_effect=probe,
        ):
            return enumerate_verified_date_adjustments(
                object(), object(), [1, 2], self.originals, self.horizon_end, {}, self.labels,
            )

    def test_returns_each_independent_project_adjustment(self):
        results = self.enumerate_with(
            lambda _db, _scheduler, changes, _kwargs: any(
                date.date() >= self.deadline.date() + timedelta(days=2)
                for date in changes.values()
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
        """有了单项目方案就不再搜多项目组合——改一个合同日永远优于改两个。"""
        calls = []

        def validator(_db, _scheduler, changes, _kwargs):
            calls.append(sorted(changes))
            return 1 in changes

        results = self.enumerate_with(validator)

        self.assertEqual([[1]], [result["projects"] for result in results])
        self.assertNotIn([1, 2], calls[1:])      # calls[0] 是预检，之后不再试组合

    def test_returns_empty_when_horizon_contains_no_solution(self):
        results = self.enumerate_with(
            lambda _db, _scheduler, _changes, _kwargs: False,
        )

        self.assertEqual([], results)

    def test_gives_up_after_one_solve_when_no_candidate_can_help(self):
        """延期是单调放松：全推到最远还排不下，就没有可行的组合可找。

        真实案例里卡住排程的项目没进候选名单，1237 次组合试探必然全部失败，
        白等满 120 秒才给出一张空白方案表。
        """
        calls = []

        def validator(_db, _scheduler, changes, _kwargs):
            calls.append(dict(changes))
            return False

        results = self.enumerate_with(validator)

        self.assertEqual([], results)
        self.assertEqual(1, len(calls))
        self.assertEqual(
            {1: self.horizon_end.date(), 2: self.horizon_end.date()},
            {project_id: date.date() for project_id, date in calls[0].items()},
        )

    def test_keeps_searching_when_the_first_probe_only_times_out(self):
        """求解超时不是"排不下"的证明，不能据此放弃搜索。

        最宽松那次探测放开了全部结题日上界，模型反而更难收敛，实测就会超时。
        若把超时当成排不下，本来存在的方案会被整批丢掉——线上正是这么漏掉了
        "某项目延 3 天"这个可行方案。
        """
        verdicts = [UNDETERMINED]

        def validator(_db, _scheduler, changes, _kwargs):
            if verdicts:
                return verdicts.pop()
            return 1 in changes

        results = self.enumerate_with(validator)

        self.assertEqual([[1]], [result["projects"] for result in results])

    def test_tries_the_earliest_deadline_project_first(self):
        """结题日最早的项目最可能是被顶破的那个，先试它。"""
        self.originals = {1: self.deadline + timedelta(days=1), 2: self.deadline}
        calls = []

        def validator(_db, _scheduler, changes, _kwargs):
            calls.append(sorted(changes))
            return len(changes) == 2

        self.enumerate_with(validator)

        self.assertEqual([1, 2], calls[0])       # 预检：全部推到最远
        self.assertEqual([2], calls[1])          # 首个单项目试探是结题日更早的 2


if __name__ == "__main__":
    unittest.main()
