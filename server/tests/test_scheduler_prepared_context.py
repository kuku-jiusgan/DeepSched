"""交期建议搜索复用排程上下文，并且只判可行性。

一次搜索要为几十上百个候选结题日各调一次 generate()。候选日期只改
project.end_date，工作日历和固定时间槽都不受影响，逐次重建纯属浪费；
落地时间槽也会被调用方回滚掉，同样不必做。
"""

import unittest
import unittest.mock

from app.services.scheduler import SchedulerService


class PreparedContextTest(unittest.TestCase):
    def test_reuse_disabled_by_default(self):
        scheduler = SchedulerService(db=None)
        calls = []

        for _ in range(3):
            scheduler._prepare("k", lambda: calls.append(1) or "v")

        self.assertEqual(3, len(calls))

    def test_reuse_builds_once_per_key(self):
        scheduler = SchedulerService(db=None, reuse_prepared_context=True)
        calls = []

        results = [
            scheduler._prepare("k", lambda: calls.append(1) or "v")
            for _ in range(3)
        ]

        self.assertEqual(1, len(calls))
        self.assertEqual(["v", "v", "v"], results)

    def test_different_keys_are_built_separately(self):
        scheduler = SchedulerService(db=None, reuse_prepared_context=True)
        calls = []

        scheduler._prepare("a", lambda: calls.append("a") or "a")
        scheduler._prepare("b", lambda: calls.append("b") or "b")
        scheduler._prepare("a", lambda: calls.append("a") or "a")

        self.assertEqual(["a", "b"], calls)


class FeasibilityOnlyTest(unittest.TestCase):
    """交期验证必须带 feasibility_only，否则每次探测都会白落地一次排程再回滚。"""

    def test_deadline_probe_uses_the_trial_mode_of_the_real_entry_point(self):
        """探测候选结题日走真实入口，且必须是不提交的试排模式。

        判定排不排得下只能有一个口径；而入口默认是会提交的，探测若不指定试排，
        改过的结题日和排程结果会被永久写进库。
        """
        from app.services.scheduler_deadline_recommendation import FEASIBLE, _probe_deadlines

        seen = {}

        def fake_apply(db, project_id, preserve_existing=False):
            seen["project_id"] = project_id
            seen["preserve_existing"] = preserve_existing
            return unittest.mock.MagicMock(status="applied")

        with unittest.mock.patch(
            "app.services.project_plan_apply_service.apply_project_plan",
            side_effect=fake_apply,
        ):
            verdict = _probe_deadlines(
                unittest.mock.MagicMock(), None, {}, {"current_project_id": 9},
            )

        self.assertEqual(FEASIBLE, verdict)
        self.assertEqual(9, seen["project_id"])
        self.assertTrue(seen["preserve_existing"])



if __name__ == "__main__":
    unittest.main()
