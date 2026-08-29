"""非工作时段仪器一律不显示运行中。

仪器占用的判定依据是"有没有一个已开始且未结束的时间槽"。技术员下班前没点完成，
任务就一直挂着运行中，仪器于是在夜里和周末也显示运行中——那个时段根本不会有人
在操作。故障与维护状态不受这条规则影响。
"""

import unittest
from types import SimpleNamespace

from app.services.lab_status_service import _reconcile_instrument_status


def _instrument(status="idle"):
    return SimpleNamespace(id=1, status=status)


class LabStatusWorkingHoursTest(unittest.TestCase):
    def test_running_slot_shows_running_inside_working_hours(self):
        instrument = _instrument()

        status = _reconcile_instrument_status(instrument, SimpleNamespace(id=9), True)

        self.assertEqual("running", status)

    def test_running_slot_shows_idle_outside_working_hours(self):
        instrument = _instrument()

        status = _reconcile_instrument_status(instrument, SimpleNamespace(id=9), False)

        self.assertEqual("idle", status)

    def test_stored_status_is_not_flipped_by_working_hours(self):
        """只改呈现，不改库里的字段——它还有别的读者，来回翻转是无谓的写入。"""
        instrument = _instrument()

        _reconcile_instrument_status(instrument, SimpleNamespace(id=9), False)

        self.assertEqual("running", instrument.status)

    def test_fault_and_maintenance_are_unaffected(self):
        for protected in ("fault", "maintenance"):
            with self.subTest(protected=protected):
                instrument = _instrument(protected)

                status = _reconcile_instrument_status(instrument, None, False)

                self.assertEqual(protected, status)

    def test_idle_instrument_stays_idle(self):
        self.assertEqual("idle", _reconcile_instrument_status(_instrument(), None, True))


if __name__ == "__main__":
    unittest.main()


class CurrentTaskClearedOutsideWorkingHoursTest(unittest.TestCase):
    """非工作时段连同"当前任务"一起清空。

    前端是按有没有当前任务来判定运行中的（LabStatusScreen 的 detailClass 与
    runningCount 都只看 current_task），只把 status 改成空闲、任务信息还挂着，
    界面上依然显示运行中，两个字段也自相矛盾。
    """

    def _payload(self, in_working_time: bool):
        from app.services.lab_status_service import _instrument_status

        instrument = SimpleNamespace(
            id=1, code="ZBYY-002-0001", name="液质联用仪", instrument_group=None,
            location=None, status="idle", label_x=0, label_y=0,
        )
        slot = SimpleNamespace(
            id=9, task=SimpleNamespace(
                id=5, name="方法开发", project_id=7, assignee_name="技术员",
            ),
        )
        status_data = {
            "projects": {7: SimpleNamespace(name="项目", code="P-001")},
            "task_windows": {},
            "next_slots": {},
        }
        from datetime import datetime as real_datetime

        return _instrument_status(
            instrument, real_datetime(2026, 8, 31, 10, 0), slot, status_data, in_working_time,
        )

    def test_current_task_is_kept_inside_working_hours(self):
        self.assertEqual("方法开发", self._payload(True)["current_task"])

    def test_current_task_is_cleared_outside_working_hours(self):
        payload = self._payload(False)

        self.assertIsNone(payload["current_task"])
        self.assertEqual("idle", payload["status"])
