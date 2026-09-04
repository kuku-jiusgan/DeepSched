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

    def test_no_plan_when_no_single_project_alone_can_succeed(self):
        """只有"两个一起延"才可行时，不输出任何方案。

        组合方案看不出每个项目为什么被牵进来，业务上也没法执行——宁可告诉人
        单独延谁都不行，也不给一张各延一天的表。
        """
        results = self.enumerate_with(
            lambda _db, _scheduler, changes, _kwargs: (
                set(changes) == {1, 2}
                and sum((date.date() - self.deadline.date()).days for date in changes.values()) >= 3
            )
        )

        self.assertEqual([], results)

    def test_never_probes_a_multi_project_combination(self):
        """除了开头那次"最宽松"预检，绝不试多项目组合。"""
        calls = []

        def validator(_db, _scheduler, changes, _kwargs):
            calls.append(sorted(changes))
            return 1 in changes

        results = self.enumerate_with(validator)

        self.assertEqual([[1]], [result["projects"] for result in results])
        self.assertTrue(all(len(item) == 1 for item in calls[1:]), calls)

    def test_plans_are_sorted_by_delay_days(self):
        """项目 2 只要延 1 天，项目 1 要延 3 天——先给代价小的那个。"""
        results = self.enumerate_with(
            lambda _db, _scheduler, changes, _kwargs: all(
                (date.date() - self.deadline.date()).days >= (3 if project_id == 1 else 1)
                for project_id, date in changes.items()
            )
        )

        self.assertEqual([[2], [1]], [result["projects"] for result in results])
        self.assertEqual([1, 3], [result["changes"][0]["delay_days"] for result in results])

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


class BinarySearchOverCandidateDatesTest(unittest.TestCase):
    """按候选日期二分，而不是逐天顺序试。

    延后结题日是单调放松：某天可行则更晚的天必然可行。逐天扫的代价全压在
    「其实没有解」的项目上——必须一路试到求解视界才能断言单独延它不行。实测
    一次搜索里三个这样的项目吃掉了 227 次试解。
    """

    def setUp(self):
        self.deadline = datetime(2026, 9, 1, 23, 59)
        self.originals = {1: self.deadline}
        self.horizon_end = self.deadline + timedelta(days=64)   # 64 个候选日期
        self.calls = []

    def enumerate_with(self, first_feasible_offset):
        def probe(_db, _scheduler, changes, _kwargs):
            self.calls.append(changes)
            offset = (next(iter(changes.values())).date() - self.deadline.date()).days
            if first_feasible_offset is None:
                # 全程超时：预检不是"证明不可行"，所以搜索会照常往下走，
                # 二分要自己把整个候选区间收完才能断言没有方案。
                return UNDETERMINED
            return FEASIBLE if offset >= first_feasible_offset else INFEASIBLE

        with patch(
            "app.services.scheduler_deadline_recommendation._probe_deadlines",
            side_effect=probe,
        ):
            return enumerate_verified_date_adjustments(
                object(), object(), [1], self.originals, self.horizon_end, {}, {1: "项目一"},
            )

    def test_finds_the_smallest_feasible_delay(self):
        results = self.enumerate_with(first_feasible_offset=37)

        self.assertEqual(1, len(results))
        self.assertEqual(37, results[0]["changes"][0]["delay_days"])

    def test_proves_no_solution_within_logarithmic_probes(self):
        results = self.enumerate_with(first_feasible_offset=None)

        self.assertEqual([], results)
        # 64 个候选：二分 7 次足够，加上开头那次最宽松预检。逐天扫要 64 次。
        self.assertLessEqual(len(self.calls), 9, len(self.calls))
