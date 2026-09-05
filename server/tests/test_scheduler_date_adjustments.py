import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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

    def test_gives_up_on_a_hopeless_project_after_two_solves(self):
        """延期是单调放松：最远那天都排不下，这个项目就没有可行日期可找。

        每次试解都是一次完整排程（实测 4.3 秒），次数就是成本。真实案例里卡住
        排程的项目没进候选名单，1237 次组合试探必然全部失败，白等满 120 秒才
        给出一张空白方案表。现在一个无解的项目最多试两次：最近那天和最远那天。
        """
        calls = []

        def validator(_db, _scheduler, changes, _kwargs):
            calls.append(dict(changes))
            return False

        results = self.enumerate_with(validator)

        self.assertEqual([], results)
        # 两个项目，各自最多"最近那天 + 最远那天"两次。
        self.assertLessEqual(len(calls), 4, calls)
        self.assertTrue(all(len(changes) == 1 for changes in calls), calls)
        self.assertEqual(
            {self.horizon_end.date()},
            {date.date() for changes in calls for date in changes.values()
             if date.date() == self.horizon_end.date()},
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

        self.assertEqual([2], calls[0])          # 首个试探是结题日更早的 2


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
                # 全程超时。超时不是"证明不可行"，但最远那天也没被证明可行，
                # 所以这个项目直接判定没有方案。
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
        # 64 个候选：最近、最远各一次，再倍增+二分收敛。逐天扫要 37 次。
        self.assertLessEqual(len(self.calls), 12, len(self.calls))

    def test_a_one_day_delay_costs_a_single_solve(self):
        """答案很小是常态，这一档不能还走满 log2(N) 次。"""
        results = self.enumerate_with(first_feasible_offset=1)

        self.assertEqual(1, results[0]["changes"][0]["delay_days"])
        self.assertEqual(1, len(self.calls), self.calls)

    def test_proves_no_solution_within_logarithmic_probes(self):
        results = self.enumerate_with(first_feasible_offset=None)

        self.assertEqual([], results)
        # 最近那天 + 最远那天就能断言，逐天扫要 64 次。
        self.assertLessEqual(len(self.calls), 2, len(self.calls))


class ProbeGoesThroughTheRealEntryPointTest(unittest.TestCase):
    """探测候选结题日必须走真实的「保存并排程」入口。

    判定排不排得下只能有一个口径。此前探测是重放一份 generate_kwargs 直接求解，
    与用户点下去真正走的那条路不等价——实测同一批候选里 12 个有 9 个判定不一致，
    全部是重放那条偏乐观，四套「已验证」的方案只有一套真能排下去。
    """

    def test_maps_real_entry_point_result_to_verdict(self):
        from app.services.scheduler_deadline_recommendation import _probe_deadlines

        calls = []

        def fake_apply(db, project_id, preserve_existing=False):
            calls.append((project_id, preserve_existing))
            return SimpleNamespace(status="applied")

        db = MagicMock()
        with patch(
            "app.services.project_plan_apply_service.apply_project_plan",
            side_effect=fake_apply,
        ):
            verdict = _probe_deadlines(
                db, object(), {1: datetime(2026, 9, 20, 23, 59)},
                {"current_project_id": 7},
            )

        self.assertEqual(FEASIBLE, verdict)
        # 必须用试排模式：不加的话入口会真的提交，外层 savepoint 拦不住，
        # 探测会把改过的结题日和排程结果永久写进库。
        self.assertEqual([(7, True)], calls)
        db.begin_nested.return_value.rollback.assert_called_once()

    def test_real_entry_point_error_means_infeasible(self):
        from app.services.scheduler_deadline_recommendation import _probe_deadlines

        with patch(
            "app.services.project_plan_apply_service.apply_project_plan",
            return_value=SimpleNamespace(status="error"),
        ):
            verdict = _probe_deadlines(
                MagicMock(), object(), {1: datetime(2026, 9, 20, 23, 59)},
                {"current_project_id": 7},
            )

        self.assertEqual(INFEASIBLE, verdict)
