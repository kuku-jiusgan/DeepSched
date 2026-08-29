"""失败时记录的求解参数必须能原样回放。

交期建议的职责是"把刚才失败的那道题换个结题日再跑一遍"。参数少带一个，解的就
不是同一道题：暂停切换曾因此被重建成"任务从头完整排一遍"（丢了只排剩余工时、
接替任务锁在当下、被暂停任务保持暂停等约束），在那道被改写的题上验证出来的方案
对真实操作并不成立——实测「延期某项目 1 天」在残缺参数下判为可行，用完整参数
重验则不可行。
"""

import inspect
import unittest

from app.services.scheduler import (
    _REPLAY_EXCLUDED_KWARGS,
    SchedulerService,
    replayable_kwargs,
)


class ReplanRequestReplayTest(unittest.TestCase):
    def _parameter_names(self) -> set[str]:
        return set(inspect.signature(SchedulerService.generate).parameters) - {"self"}

    def test_every_parameter_is_either_replayed_or_deliberately_excluded(self):
        """新增求解参数时必须明确表态：要么回放，要么写进排除表。

        此前 replan_request 是手挑字段拼出来的，新增参数不会被记录也不会报错，
        验证于是悄悄解上了另一道题。
        """
        names = self._parameter_names()

        unaccounted = names - _REPLAY_EXCLUDED_KWARGS
        self.assertTrue(unaccounted, "至少要有可回放的参数")
        self.assertEqual(set(), _REPLAY_EXCLUDED_KWARGS - names, "排除表里有已不存在的参数")

    def test_replay_snapshot_covers_the_pause_switch_constraints(self):
        """暂停切换真正依赖的那几个参数必须在回放范围内。"""
        replayable = self._parameter_names() - _REPLAY_EXCLUDED_KWARGS

        for name in (
            "remaining_duration_minutes",   # 只排剩余工时
            "planning_start_at",            # 从切换时刻起排
            "planning_end_at",
            "replaceable_after",
            "preserved_status_task_ids",    # 被暂停任务保持暂停
            "preserved_slot_ids",           # 接替任务锁在当下
            "setup_exempt_task_pairs",
            "fixed_instrument_ids",
        ):
            self.assertIn(name, replayable, f"{name} 必须被记录，否则验证解的不是同一道题")

    def test_snapshot_drops_none_and_excluded_values(self):
        scope = {
            "self": None, "project_ids": [1], "task_ids": None,
            "commit": True, "current_project_id": 9, "mode": "insert",
        }
        for name in self._parameter_names():
            scope.setdefault(name, None)

        snapshot = replayable_kwargs(scope)

        self.assertEqual([1], snapshot["project_ids"])
        self.assertEqual("insert", snapshot["mode"])
        self.assertNotIn("task_ids", snapshot)          # None 不记录
        self.assertNotIn("commit", snapshot)            # 验证自己决定
        self.assertNotIn("current_project_id", snapshot)


if __name__ == "__main__":
    unittest.main()
