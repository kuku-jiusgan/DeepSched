"""重排在求解前删除的时间槽，仍要按原计划位置计入仪器占用。

否则排程失败的占用明细里，被顺延项目的仪器占用会凭空变成 0，工时被记进
预测工时列，缺口数字也跟着偏。
"""

import unittest
from datetime import datetime
from types import SimpleNamespace

from app.services.scheduler_failure_diagnostics import (
    _project_instrument_intervals,
    _released_task_spans,
)


def _instrument(instrument_id=101):
    return SimpleNamespace(id=instrument_id, name="测试仪器", code="CSYQ")


def _task(task_id, project, hours=8, requires_instrument=True, slots=()):
    return SimpleNamespace(
        id=task_id, name=f"任务{task_id}", project=project, project_id=project.id,
        parent=None, instrument_ids=[101], assignee_id=1, status="pending",
        requires_instrument=requires_instrument, requires_human=True,
        est_duration_hours=hours, switchover_hours=0, executed_minutes=0,
        additional_planned_minutes=0, time_slots=list(slots), latest_due=None,
        execution_segments=[],
    )


class ReleasedSlotOccupancyTest(unittest.TestCase):
    def setUp(self):
        self.window_start = datetime(2026, 8, 28, 8, 30)
        self.window_end = datetime(2026, 9, 7, 20, 0)
        self.project = SimpleNamespace(
            id=1, name="测试项目A", code="测试项目A",
            start_date=self.window_start, end_date=self.window_end,
        )
        self.task = _task(10, self.project)
        self.compatibility = {10: [_instrument()]}

    def _breakdown(self, released):
        _intervals, breakdown = _project_instrument_intervals(
            [self.task], 101, self.compatibility,
            self.window_start, self.window_end, (), released,
        )
        return breakdown

    def test_deleted_slots_still_count_as_instrument_occupancy(self):
        released = {10: [
            (datetime(2026, 8, 31, 8, 30), datetime(2026, 8, 31, 20, 0), 101),
            (datetime(2026, 9, 1, 8, 30), datetime(2026, 9, 1, 20, 0), 101),
        ]}

        breakdown = self._breakdown(released)

        self.assertEqual(23.0, breakdown["slot"])
        self.assertEqual(0, breakdown["forecast"])

    def test_without_the_snapshot_the_hours_fall_into_forecast(self):
        breakdown = self._breakdown({})

        self.assertEqual(0, breakdown["slot"])
        self.assertGreater(breakdown["forecast"], 0)

    def test_other_instruments_are_not_counted(self):
        released = {10: [
            (datetime(2026, 8, 31, 8, 30), datetime(2026, 8, 31, 20, 0), 999),
        ]}

        self.assertEqual(0, self._breakdown(released)["slot"])


class ReleasedTaskSpansTest(unittest.TestCase):
    def test_overlapping_spans_are_merged(self):
        released = {7: [
            (datetime(2026, 8, 31, 8, 30), datetime(2026, 8, 31, 14, 0), 101),
            (datetime(2026, 8, 31, 13, 0), datetime(2026, 8, 31, 20, 0), 101),
        ]}

        spans = _released_task_spans(released, 7, 101)

        self.assertEqual(
            [(datetime(2026, 8, 31, 8, 30), datetime(2026, 8, 31, 20, 0))], spans,
        )

    def test_no_instrument_filter_keeps_every_span(self):
        released = {7: [(datetime(2026, 8, 31, 8, 30), datetime(2026, 8, 31, 11, 0), None)]}

        self.assertEqual(1, len(_released_task_spans(released, 7, None)))

    def test_missing_task_returns_empty(self):
        self.assertEqual([], _released_task_spans({}, 7, 101))


if __name__ == "__main__":
    unittest.main()
