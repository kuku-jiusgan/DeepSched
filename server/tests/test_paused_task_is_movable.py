import unittest

from app.services.schedule_replan_closure_service import MOVABLE_TASK_STATUSES
from app.services.scheduler_persistence import (
    IMMOVABLE_EXECUTION_STATUSES,
    STATUS_PRESERVED_EXECUTION_STATUSES,
    _persisted_task_status,
)


class Task:
    def __init__(self, status):
        self.status = status


class PausedTaskIsMovableTest(unittest.TestCase):
    """暂停任务的位置可以被重排移动，必须保住的只是它的状态。

    只有正在跑的任务位置不能动——它此刻真的在仪器上跑着。把暂停也一起冻住，
    时间槽就钉死在原地，别的任务只能绕着排。
    """

    def test_only_running_tasks_are_position_locked(self):
        self.assertEqual({"running"}, IMMOVABLE_EXECUTION_STATUSES)
        self.assertIn("paused", STATUS_PRESERVED_EXECUTION_STATUSES)
        self.assertIn("interrupted", STATUS_PRESERVED_EXECUTION_STATUSES)
        self.assertNotIn("paused", IMMOVABLE_EXECUTION_STATUSES)

    def test_paused_task_enters_the_replan_closure(self):
        self.assertIn("paused", MOVABLE_TASK_STATUSES)
        self.assertIn("interrupted", MOVABLE_TASK_STATUSES)

    def test_preserved_task_keeps_its_status_on_persist(self):
        self.assertEqual("paused", _persisted_task_status(Task("paused"), True))
        # 正在跑的任务即使被显式保留，落库的时间槽仍然按待排写，避免新槽一出生就是运行中。
        self.assertEqual("scheduled", _persisted_task_status(Task("running"), True))
        self.assertEqual("scheduled", _persisted_task_status(Task("paused"), False))


if __name__ == "__main__":
    unittest.main()
