import threading
import unittest
from types import SimpleNamespace

from app.services.schedule_run_lock_service import (
    DEADLINE_RECOMMENDATION,
    SCHEDULE_RUN,
    ScheduleBusyError,
    busy_message,
    current_activity,
    schedule_run_lock,
)


class ScheduleRunLockTest(unittest.TestCase):
    """排程必须互斥，且撞车时当场退回而不是干等到数据库锁超时。

    线上事故：后台在为一次排程失败搜索结题日调整方案，用户重复点"保存并排程"，
    两次请求都在 UPDATE task 上等满 innodb_lock_wait_timeout（50 秒）才抛 1205，
    前端只显示一句"失败"——看不出是撞了车还是真的排不下。
    """

    def run_in_other_thread(self, func):
        outcome = {}

        def target():
            try:
                outcome["value"] = func()
            except Exception as exc:                     # noqa: BLE001 - 测试要看到异常本身
                outcome["error"] = exc

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "抢锁必须立即返回，不允许阻塞等待")
        return outcome

    def try_lock_in_other_thread(self, activity):
        """在另一条线程里完整地抢一次锁再放掉，不把锁留给已经结束的线程。"""
        def attempt():
            with schedule_run_lock(activity):
                return "acquired"

        return self.run_in_other_thread(attempt)

    def test_second_run_is_refused_immediately_with_a_readable_reason(self):
        with schedule_run_lock(DEADLINE_RECOMMENDATION):
            outcome = self.try_lock_in_other_thread(SCHEDULE_RUN)

        error = outcome.get("error")
        self.assertIsInstance(error, ScheduleBusyError)
        self.assertIn(DEADLINE_RECOMMENDATION, str(error))
        self.assertIn("请稍后重试", str(error))

    def test_lock_is_released_for_the_next_run(self):
        with schedule_run_lock(SCHEDULE_RUN):
            pass

        self.assertIsNone(current_activity())
        with schedule_run_lock(DEADLINE_RECOMMENDATION):
            self.assertEqual(DEADLINE_RECOMMENDATION, current_activity())

    def test_is_reentrant_within_one_run(self):
        """方案搜索本身就是一次持锁的排程，内部还要反复调用求解器。"""
        with schedule_run_lock(DEADLINE_RECOMMENDATION):
            with schedule_run_lock(SCHEDULE_RUN):
                # 嵌套不改写占用者，外层活动名才是要报给用户的那个。
                self.assertEqual(DEADLINE_RECOMMENDATION, current_activity())
            self.assertEqual(DEADLINE_RECOMMENDATION, current_activity())

        self.assertIsNone(current_activity())

    def test_lock_is_released_even_when_the_run_fails(self):
        with self.assertRaises(ValueError):
            with schedule_run_lock(SCHEDULE_RUN):
                raise ValueError("求解炸了")

        self.assertIsNone(current_activity())
        outcome = self.try_lock_in_other_thread(SCHEDULE_RUN)
        self.assertEqual("acquired", outcome.get("value"))

    def test_busy_message_reports_the_running_activity(self):
        with schedule_run_lock(DEADLINE_RECOMMENDATION):
            self.assertIn(DEADLINE_RECOMMENDATION, busy_message())


class DeadlineRecommendationYieldsToUserTest(unittest.TestCase):
    """用户排程优先：方案搜索抢不到锁就把作业留在 pending，下一轮再捡。

    作业不能因此判失败——失败会让前端显示"调整方案暂未生成"，而它其实一次都
    还没算过。
    """

    def test_job_stays_pending_when_a_schedule_run_holds_the_lock(self):
        from unittest.mock import patch

        from app.services import schedule_deadline_recommendation_job_service as service

        job = SimpleNamespace(id="job-1", status="pending", started_at=None)

        class _Query:
            def filter(self, *_args):
                return self

            def order_by(self, *_args):
                return self

            def first(self):
                return job

        db = SimpleNamespace(query=lambda _model: _Query())

        holding, release = threading.Event(), threading.Event()

        def hold_the_lock():
            with schedule_run_lock(SCHEDULE_RUN):
                holding.set()
                release.wait(timeout=5)

        holder = threading.Thread(target=hold_the_lock)
        holder.start()
        self.assertTrue(holding.wait(timeout=5))
        try:
            with patch.object(service, "_run_job") as run_job:
                service._process_next_job(db)
        finally:
            release.set()
            holder.join(timeout=5)

        run_job.assert_not_called()
        self.assertEqual("pending", job.status)
        self.assertIsNone(job.started_at)


if __name__ == "__main__":
    unittest.main()
