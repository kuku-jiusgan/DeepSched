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

    def test_deadline_search_asks_only_for_feasibility(self):
        from app.services.scheduler_deadline_recommendation import FEASIBLE, _probe_deadlines

        class _Scheduler:
            kwargs = None

            def generate(self, **kwargs):
                _Scheduler.kwargs = kwargs
                return {"status": "ok"}

        class _Db:
            def begin_nested(self):
                return unittest.mock.MagicMock()

            def flush(self):
                pass

        self.assertEqual(FEASIBLE, _probe_deadlines(_Db(), _Scheduler(), {}, {"project_ids": [1]}))
        self.assertTrue(_Scheduler.kwargs["feasibility_only"])
        self.assertFalse(_Scheduler.kwargs["include_failure_diagnostics"])
        self.assertFalse(_Scheduler.kwargs["commit"])


if __name__ == "__main__":
    unittest.main()
