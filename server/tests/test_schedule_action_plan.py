"""求解结果先变成一份计划，再由既有落盘服务执行。

原先"要往库里写什么"和"怎么写"是揉在一起的：persist_slots 一边从求解器读值、一边
db.add、一边改任务状态、一边重建桥接。于是这份结果没法先看一眼再决定要不要采纳——
探测只能靠 savepoint 包住整段写操作再回滚。

拆开之后，计划是一个纯值：可以检查、可以丢弃、可以序列化后交给另一个进程。
"""

import pickle
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.schedule_action_plan import (
    CreateSlot,
    SchedulePlan,
    SetTaskStatus,
    build_schedule_plan,
)


class _Solver:
    def __init__(self, values):
        self.values = values

    def Value(self, key):
        return self.values[key]


def _task(task_id, **kwargs):
    return SimpleNamespace(
        id=task_id, name="任务%d" % task_id, status=kwargs.get("status", "pending"),
        requires_instrument=kwargs.get("requires_instrument", False),
        allow_split=kwargs.get("allow_split", False),
    )


class SchedulePlanIsAValueTest(unittest.TestCase):
    def test_plan_is_immutable_and_serializable(self):
        plan = SchedulePlan(
            schedule_run_id="run-1",
            frozen_boundary=datetime(2026, 9, 6),
            confirmed_boundary=datetime(2026, 9, 12),
            slots=(CreateSlot(1, 10, datetime(2026, 9, 7, 8, 30),
                              datetime(2026, 9, 7, 12, 0), "scheduled"),),
            task_statuses=(SetTaskStatus(1, "scheduled"),),
        )

        self.assertEqual(plan, pickle.loads(pickle.dumps(plan)))
        with self.assertRaises(Exception):
            plan.schedule_run_id = "run-2"


class BuildScheduleplanTouchesNothingTest(unittest.TestCase):
    """算计划的过程只读求解器取值，不碰数据库、不改任何对象。"""

    def setUp(self):
        self.horizon_start = datetime(2026, 9, 7, 8, 30)
        self.working_context = SimpleNamespace(
            policy_for=lambda instrument_id: SimpleNamespace(
                day_start_minutes=8 * 60 + 30, day_end_minutes=20 * 60,
                include_weekends=True, include_holidays=True,
            ),
            calendar_days={},
        )

    def _build(self, tasks, **kwargs):
        defaults = dict(
            tasks=tasks, instruments=[], solver=_Solver(kwargs.pop("values", {})),
            task_starts=kwargs.pop("task_starts", {}),
            task_ends=kwargs.pop("task_ends", {}),
            presences={}, split_unit_presences={},
            horizon_start=self.horizon_start, working_context=self.working_context,
            schedule_run_id="run-1",
            frozen_boundary=self.horizon_start - timedelta(days=1),
            confirmed_boundary=self.horizon_start + timedelta(days=7),
            forecast_task_ids=set(), preserved_status_task_ids=set(),
            immovable_statuses={"running"}, preserved_statuses={"paused", "interrupted"},
        )
        defaults.update(kwargs)
        return build_schedule_plan(**defaults)

    def test_a_plain_task_becomes_one_slot_and_one_status_change(self):
        task = _task(1)
        plan = self._build(
            [task], values={"s": 0, "e": 4}, task_starts={1: "s"}, task_ends={1: "e"},
        )

        self.assertEqual(1, len(plan.slots))
        self.assertEqual(self.horizon_start, plan.slots[0].plan_start)
        self.assertEqual(self.horizon_start + timedelta(hours=2), plan.slots[0].plan_end)
        self.assertEqual((SetTaskStatus(1, "scheduled"),), plan.task_statuses)
        # 任务对象本身不能被改动——改状态是执行阶段的事。
        self.assertEqual("pending", task.status)

    def test_a_paused_task_keeps_its_status(self):
        """暂停任务的位置可以被重排，但状态不能被改写成待执行。"""
        plan = self._build(
            [_task(1, status="paused")],
            values={"s": 0, "e": 2}, task_starts={1: "s"}, task_ends={1: "e"},
        )

        self.assertEqual("paused", plan.slots[0].status)
        self.assertEqual((), plan.task_statuses)

    def test_a_running_task_is_left_alone(self):
        plan = self._build(
            [_task(1, status="running")],
            values={"s": 0, "e": 2}, task_starts={1: "s"}, task_ends={1: "e"},
        )

        self.assertEqual((), plan.slots)
        self.assertEqual((), plan.task_statuses)

    def test_an_unapproved_downstream_task_gets_no_slot(self):
        """未签批方案的下游任务只占产能，不落地时间槽。"""
        plan = self._build(
            [_task(1)], values={"s": 0, "e": 2}, task_starts={1: "s"},
            task_ends={1: "e"}, forecast_task_ids={1},
        )

        self.assertEqual((), plan.slots)


if __name__ == "__main__":
    unittest.main()
