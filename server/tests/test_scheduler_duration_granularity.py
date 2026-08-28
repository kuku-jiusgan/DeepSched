"""诊断层的工时口径必须和求解器一致，都按 30 分钟单元。

缺口分析（第一层）若比求解器（第二层）乐观，就会出现"第一层判定工时够用、
按它给的方案改完日期后第二层仍然排不下"。
"""

import unittest
from datetime import datetime
from types import SimpleNamespace

from app.services.scheduler_failure_diagnostics import _remaining_task_hours
from app.services.scheduler_helpers import task_duration_hours, task_duration_units
from app.services.scheduler_instrument_bridging import bridged_instrument_hours


def _task(task_id, assignee_id, requires_instrument, hours=2, switchover=0):
    return SimpleNamespace(
        id=task_id,
        assignee_id=assignee_id,
        requires_human=True,
        requires_instrument=requires_instrument,
        est_duration_hours=hours,
        switchover_hours=switchover,
    )


class TaskDurationUnitsTest(unittest.TestCase):
    def test_rounds_up_to_thirty_minute_units(self):
        self.assertEqual(4, task_duration_units(_task(1, 7, True, hours=1.7)))
        self.assertEqual(2.0, task_duration_hours(_task(1, 7, True, hours=1.7)))

    def test_switchover_is_rounded_separately_like_the_solver(self):
        # 求解器对时长和切换时间分别向上取整，诊断必须复刻同一口径。
        self.assertEqual(4, task_duration_units(_task(1, 7, True, hours=1.2, switchover=0.2)))

    def test_zero_switchover_is_not_inflated_to_one_unit(self):
        self.assertEqual(16, task_duration_units(_task(1, 7, True, hours=8, switchover=0)))


class BridgedInstrumentHoursTest(unittest.TestCase):
    def test_bridged_hours_are_quantised(self):
        previous = _task(1, 7, True)
        manual = _task(2, 7, False, hours=2.2)
        following = _task(3, 7, True)
        tasks = [previous, manual, following]
        dependencies = [(2, 1), (3, 2)]
        instrument = SimpleNamespace(id=101)
        compatibility = {1: [instrument], 2: [], 3: [instrument]}

        hours = bridged_instrument_hours(tasks, dependencies, compatibility, 101)

        # 2.2h 在求解器里占 5 个单元 = 2.5h，缺口分析不能只记 2.2h。
        self.assertEqual(2.5, hours)


class RemainingTaskHoursTest(unittest.TestCase):
    """剩余工时按累计有效执行分钟数扣减，不按 execution_segments 的墙钟跨度。"""

    def _started_friday_ended_monday(self, executed_minutes):
        return SimpleNamespace(
            status="running",
            est_duration_hours=8,
            switchover_hours=0,
            executed_minutes=executed_minutes,
            execution_segments=[SimpleNamespace(
                started_at=datetime(2026, 8, 28, 18, 0),
                ended_at=datetime(2026, 8, 31, 10, 0),
            )],
        )

    def test_nights_and_weekends_are_not_counted_as_executed(self):
        # 墙钟跨度 64 小时，旧口径会把 8 小时的任务判成已做完。
        task = self._started_friday_ended_monday(executed_minutes=120)

        self.assertEqual(6.0, _remaining_task_hours(task))

    def test_approved_delay_hours_are_included(self):
        task = SimpleNamespace(
            status="pending",
            est_duration_hours=8,
            switchover_hours=0,
            executed_minutes=0,
            additional_planned_minutes=120,
            execution_segments=[],
        )

        self.assertEqual(10.0, _remaining_task_hours(task))

    def test_completed_task_has_no_remaining_hours(self):
        task = SimpleNamespace(
            status="completed", est_duration_hours=8, switchover_hours=0,
            executed_minutes=0, execution_segments=[],
        )

        self.assertEqual(0, _remaining_task_hours(task))


if __name__ == "__main__":
    unittest.main()


class TaskHoursInputRoundingTest(unittest.TestCase):
    """工时在写入时就取整到排程颗粒度，入口值与求解器建模值才对得上。"""

    def test_rounds_up_to_half_hour(self):
        from app.services.task_hours_service import round_up_to_time_unit

        self.assertEqual(2.0, round_up_to_time_unit(1.7))
        self.assertEqual(1.5, round_up_to_time_unit(1.2))
        self.assertEqual(8.0, round_up_to_time_unit(8))

    def test_none_and_non_positive(self):
        from app.services.task_hours_service import round_up_to_time_unit

        self.assertIsNone(round_up_to_time_unit(None))
        self.assertEqual(0.0, round_up_to_time_unit(0))

    def test_matches_the_solver_time_unit(self):
        from app.services.scheduler_helpers import TIME_UNIT_MINUTES
        from app.services.task_hours_service import TIME_UNIT_HOURS

        self.assertEqual(TIME_UNIT_MINUTES / 60, TIME_UNIT_HOURS)

    def test_rounded_input_removes_the_double_rounding_inflation(self):
        from app.services.scheduler_helpers import task_duration_units
        from app.services.task_hours_service import round_up_to_time_unit

        task = _task(1, 7, True,
                     hours=round_up_to_time_unit(1.2),
                     switchover=round_up_to_time_unit(0.2))

        # 1.5h + 0.5h = 2.0h，两次取整与合并取整结果一致。
        self.assertEqual(4, task_duration_units(task))
