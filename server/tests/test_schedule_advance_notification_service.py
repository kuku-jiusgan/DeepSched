import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import AlertRule, Notification, Task, TimeSlot, User
from app.services.schedule_advance_notification_service import (
    _format_time_change,
    _should_deliver_externally,
    capture_task_schedule_windows,
    notify_rescheduled_tasks_advanced,
)


class ScheduleAdvanceNotificationServiceTest(unittest.TestCase):
    def test_formats_schedule_change_in_hours_or_days(self):
        self.assertEqual("3 小时", _format_time_change(3 * 3600))
        self.assertEqual("24 小时", _format_time_change(24 * 3600))
        self.assertEqual("2 天", _format_time_change(48 * 3600))

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.assignee = User(
            username="analyst",
            display_name="任务负责人",
            role="分析员",
            is_active=True,
        )
        self.task = Task(
            project_id=1,
            name="LCMS方法开发",
            task_type="method",
            status="scheduled",
            assignee=self.assignee,
        )
        self.rule = AlertRule(
            name="任务提前前移通知",
            rule_type="task_schedule_advanced",
            enabled=True,
            notify_roles='["任务负责人"]',
        )
        self.db.add_all([self.assignee, self.task, self.rule])
        self.db.flush()
        self.slot = TimeSlot(
            task_id=self.task.id,
            plan_start=datetime(2026, 7, 20, 9, 0),
            plan_end=datetime(2026, 7, 20, 11, 0),
            status="scheduled",
        )
        self.db.add(self.slot)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.services.push_notification_service.enqueue_wecom_delivery")
    def test_notifies_assignee_when_reschedule_moves_task_earlier(self, enqueue):
        original_windows = capture_task_schedule_windows(self.db, {self.task.id})
        self.slot.plan_start = datetime(2026, 7, 18, 9, 0)
        self.slot.plan_end = datetime(2026, 7, 18, 11, 0)
        self.db.flush()

        sent = notify_rescheduled_tasks_advanced(
            self.db,
            original_windows,
            "全局重排",
        )
        self.db.flush()

        notifications = self.db.query(Notification).order_by(Notification.id).all()
        self.assertEqual(1, sent)
        self.assertEqual(["site", "wecom"], [item.channel for item in notifications])
        self.assertTrue(all(item.user_name == "analyst" for item in notifications))
        self.assertEqual("任务前移通知", notifications[0].title)
        self.assertIn("您的任务：LCMS方法开发", notifications[0].content)
        self.assertIn("新时间：7/18（周六）09:00–11:00（2小时）", notifications[0].content)
        self.assertIn("原时间：7/20 09:00–11:00（已提前）", notifications[0].content)
        self.assertIn("原因：排程调整：全局重排。", notifications[0].content)
        enqueue.assert_called_once()

    @patch("app.services.push_notification_service.enqueue_wecom_delivery")
    def test_does_not_notify_when_start_time_is_unchanged_or_later(self, enqueue):
        original_windows = capture_task_schedule_windows(self.db, {self.task.id})
        self.slot.plan_start = datetime(2026, 7, 21, 9, 0)
        self.slot.plan_end = datetime(2026, 7, 21, 11, 0)
        self.db.flush()

        sent = notify_rescheduled_tasks_advanced(self.db, original_windows, "项目重排")

        self.assertEqual(0, sent)
        self.assertEqual(0, self.db.query(Notification).count())
        enqueue.assert_not_called()

    @patch("app.services.push_notification_service.enqueue_wecom_delivery")
    def test_far_future_adjustment_is_kept_on_site_without_external_push(self, enqueue):
        original_windows = capture_task_schedule_windows(self.db, {self.task.id})
        future_start = datetime.now() + timedelta(days=10)
        self.slot.plan_start = future_start
        self.slot.plan_end = future_start + timedelta(hours=2)
        original_windows[self.task.id] = (
            future_start + timedelta(days=1),
            future_start + timedelta(days=1, hours=2),
        )
        self.db.flush()

        sent = notify_rescheduled_tasks_advanced(self.db, original_windows, "远期重排")
        notifications = self.db.query(Notification).all()

        self.assertEqual(1, sent)
        self.assertEqual(["site"], [item.channel for item in notifications])
        enqueue.assert_not_called()

    def test_blocked_task_is_pushed_even_outside_48_hour_window(self):
        self.task.status = "blocked"
        start = datetime.now() + timedelta(days=10)

        self.assertTrue(_should_deliver_externally(
            self.task,
            (start, start + timedelta(hours=2)),
        ))


if __name__ == "__main__":
    unittest.main()
