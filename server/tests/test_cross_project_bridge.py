"""桥接按仪器排队顺序判定，跨项目。

同一台仪器上可能排着多个项目、同一负责人的任务。依赖边只存在于项目内部，沿
依赖边找前后任务识别不了跨项目的桥接，会低估仪器占用。
"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.scheduler_instrument_bridging import scheduled_bridge_task_ids


INSTRUMENT = 101
BASE = datetime(2026, 9, 7, 8, 30)


def _slot(offset_hours, hours, instrument_id):
    return SimpleNamespace(
        plan_start=BASE + timedelta(hours=offset_hours),
        plan_end=BASE + timedelta(hours=offset_hours + hours),
        instrument_id=instrument_id,
        lifecycle_status="active",
    )


def _task(task_id, assignee_id, requires_instrument, offset_hours, hours, instrument_id=None):
    return SimpleNamespace(
        id=task_id, name=f"任务{task_id}", assignee_id=assignee_id,
        requires_human=True, requires_instrument=requires_instrument,
        time_slots=[_slot(offset_hours, hours, instrument_id)],
    )


class CrossProjectBridgeTest(unittest.TestCase):
    def test_bridge_spans_two_projects(self):
        # A-方法开发 → A-方案撰写 → B-方法验证，全是同一个负责人。
        develop = _task(1, 7, True, 0, 4, INSTRUMENT)
        drafting = _task(2, 7, False, 4, 2, None)
        verify = _task(3, 7, True, 6, 4, INSTRUMENT)

        bridged = scheduled_bridge_task_ids([develop, drafting, verify], INSTRUMENT)

        self.assertEqual({drafting.id}, bridged)

    def test_another_persons_instrument_task_breaks_the_bridge(self):
        # 张三-方法开发 → 张三-方案撰写 → 李四-检测 → 张三-方法验证：
        # 仪器在张三写方案期间被李四用了，不算张三占用。
        develop = _task(1, 7, True, 0, 4, INSTRUMENT)
        drafting = _task(2, 7, False, 4, 2, None)
        other = _task(3, 9, True, 6, 2, INSTRUMENT)
        verify = _task(4, 7, True, 8, 4, INSTRUMENT)

        bridged = scheduled_bridge_task_ids([develop, drafting, other, verify], INSTRUMENT)

        self.assertEqual(set(), bridged)

    def test_different_assignee_is_not_a_bridge(self):
        develop = _task(1, 7, True, 0, 4, INSTRUMENT)
        drafting = _task(2, 9, False, 4, 2, None)
        verify = _task(3, 7, True, 6, 4, INSTRUMENT)

        self.assertEqual(set(), scheduled_bridge_task_ids([develop, drafting, verify], INSTRUMENT))

    def test_trailing_manual_task_is_not_a_bridge(self):
        # 报告撰写后面没有仪器任务接续，仪器已经释放。
        develop = _task(1, 7, True, 0, 4, INSTRUMENT)
        report = _task(2, 7, False, 4, 2, None)

        self.assertEqual(set(), scheduled_bridge_task_ids([develop, report], INSTRUMENT))

    def test_other_instrument_does_not_bridge(self):
        develop = _task(1, 7, True, 0, 4, 999)
        drafting = _task(2, 7, False, 4, 2, None)
        verify = _task(3, 7, True, 6, 4, 999)

        self.assertEqual(set(), scheduled_bridge_task_ids([develop, drafting, verify], INSTRUMENT))

    def test_chain_of_three_projects(self):
        # A-方法开发 → A-方案撰写 → B-方法验证 → B-报告撰写 → C-方法开发
        tasks = [
            _task(1, 7, True, 0, 4, INSTRUMENT),
            _task(2, 7, False, 4, 2, None),
            _task(3, 7, True, 6, 4, INSTRUMENT),
            _task(4, 7, False, 10, 2, None),
            _task(5, 7, True, 12, 4, INSTRUMENT),
        ]

        self.assertEqual({2, 4}, scheduled_bridge_task_ids(tasks, INSTRUMENT))


if __name__ == "__main__":
    unittest.main()
