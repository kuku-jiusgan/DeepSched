import unittest
from datetime import datetime
from types import SimpleNamespace

from app.api.schedules import _slot_execution_status


def task(status: str, segments=()):
    return SimpleNamespace(status=status, execution_segments=list(segments))


def slot(status: str, actual_start=None, actual_end=None):
    return SimpleNamespace(status=status, actual_start=actual_start, actual_end=actual_end)


class SlotExecutionStatusTest(unittest.TestCase):
    """时间槽的显示状态跟着任务当前状态走，历史段不按各自结果单独显示。

    任务被按天切成多段时，已经跑完的段并不显示"已完成"，而是跟着任务显示运行中；
    被暂停打断的段同理。口径统一在任务上，不要只把其中一类历史段单独冻住。
    """

    def test_every_segment_of_a_running_task_shows_running(self):
        finished = slot("completed", actual_start=datetime(2026, 8, 31, 9, 0),
                        actual_end=datetime(2026, 8, 31, 20, 0))
        interrupted = slot("paused", actual_start=datetime(2026, 9, 1, 8, 30),
                           actual_end=datetime(2026, 9, 1, 10, 3))

        self.assertEqual("running", _slot_execution_status(finished, task("running")))
        self.assertEqual("running", _slot_execution_status(interrupted, task("running")))

    def test_segments_follow_a_paused_task(self):
        finished = slot("completed", actual_start=datetime(2026, 9, 1, 10, 30),
                        actual_end=datetime(2026, 9, 1, 20, 0))

        self.assertEqual("paused", _slot_execution_status(finished, task("paused")))

    def test_running_and_scheduled_slots_keep_their_own_state(self):
        running = slot("running", actual_start=datetime(2026, 9, 1, 8, 30))
        self.assertEqual("running", _slot_execution_status(running, task("paused")))
        self.assertEqual("scheduled", _slot_execution_status(slot("scheduled"), task("paused")))


if __name__ == "__main__":
    unittest.main()
