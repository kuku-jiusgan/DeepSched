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
