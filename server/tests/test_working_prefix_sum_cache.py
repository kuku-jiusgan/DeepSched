import unittest
from datetime import datetime

from app.services.scheduler_helpers import (
    apply_maintenance_windows,
    build_working_flags,
    build_working_prefix_sum,
    prefix_sum_from_flags,
)


class WorkingPrefixSumDecompositionTest(unittest.TestCase):
    """拆分后的三段必须与原来的一次性构建等价。

    拆分的目的是让「不含维护窗口的基础标记」可以按工作时段策略缓存——26 台仪器
    通常只有一两种策略，逐台重算等于把整个视野的日期运算做二十几遍。维护窗口是
    按仪器不同的，所以必须留在缓存之外，且每台仪器要在副本上叠加。
    """

    def setUp(self):
        self.start = datetime(2026, 9, 1, 0, 0)
        self.units = 96 * 3
        self.args = (self.start, self.units, 8 * 60 + 30, 20 * 60)

    def test_decomposition_matches_single_shot_build(self):
        windows = [(1, (10, 20)), (1, (40, 44))]

        combined = build_working_prefix_sum(*self.args, windows, {}, False, False)
        flags = apply_maintenance_windows(
            list(build_working_flags(*self.args, {}, False, False)), self.units, windows,
        )

        self.assertEqual(combined, prefix_sum_from_flags(flags, self.units))

    def test_cached_base_is_not_mutated_by_maintenance(self):
        """缓存的基础数组必须先复制再叠加，否则一台仪器会污染其他仪器。"""
        base = build_working_flags(*self.args, {}, False, False)
        snapshot = list(base)

        apply_maintenance_windows(list(base), self.units, [(1, (10, 20))])

        self.assertEqual(snapshot, base)

    def test_two_instruments_sharing_a_policy_differ_only_by_maintenance(self):
        base = build_working_flags(*self.args, {}, False, False)
        without = prefix_sum_from_flags(list(base), self.units)
        with_window = prefix_sum_from_flags(
            apply_maintenance_windows(list(base), self.units, [(1, (10, 20))]), self.units,
        )

        # 维护窗口覆盖的单元里有 10 个原本是工作时段（8:30 起），扣掉后总量应减少
        self.assertLess(with_window[-1], without[-1])
        self.assertEqual(without[:10], with_window[:10])


if __name__ == "__main__":
    unittest.main()
