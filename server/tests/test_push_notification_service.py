import unittest
from itertools import product
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import AlertRule, Notification, PushChannelConfig, User
from app.services.push_notification_service import push_by_rule
from app.services.wecom_delivery_service import (
    WeComSendResult,
    _process_pending_wecom_notifications,
)


class PushNotificationServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_notify_roles_filter_contextual_recipients(self):
        admin = User(
            username="admin",
            display_name="系统管理员",
            role="系统管理员",
            is_active=True,
        )
        analyst = User(
            username="analyst",
            display_name="分析员",
            role="分析员",
            is_active=True,
        )
        disabled_analyst = User(
            username="disabled",
            display_name="停用分析员",
            role="分析员",
            is_active=False,
        )
        rule = AlertRule(
            name="仪器故障后移",
            rule_type="instrument_fault_reschedule",
            enabled=True,
            enable_site=True,
            enable_wecom=False,
            notify_roles='["分析员"]',
        )
        self.db.add_all([admin, analyst, disabled_analyst, rule])
        self.db.commit()

        sent = push_by_rule(
            self.db,
            "instrument_fault_reschedule",
            [admin, analyst, disabled_analyst],
            "测试通知",
            "测试内容",
        )
        self.db.flush()

        notifications = self.db.query(Notification).order_by(Notification.id).all()
        self.assertEqual(sent, 1)
        self.assertEqual([item.user_name for item in notifications], ["analyst"])
        self.assertEqual([item.channel for item in notifications], ["site"])
        self.assertEqual([item.delivery_status for item in notifications], ["success"])

    def test_project_manager_context_is_not_a_user_role(self):
        manager = User(
            username="manager",
            display_name="项目负责人",
            role="分析所所长",
            is_active=True,
        )
        rule = AlertRule(
            name="故障排程冲突",
            rule_type="instrument_fault_schedule_conflict",
            enabled=True,
            enable_site=True,
            enable_wecom=False,
            notify_roles='["项目负责人"]',
        )
        self.db.add_all([manager, rule])
        self.db.commit()

        sent = push_by_rule(
            self.db,
            "instrument_fault_schedule_conflict",
            [manager],
            "测试通知",
            "测试内容",
            context_roles=["项目负责人"],
        )
        self.db.flush()

        self.assertEqual(sent, 1)
        notifications = self.db.query(Notification).order_by(Notification.id).all()
        self.assertEqual([item.user_name for item in notifications], ["manager"])
        self.assertEqual([item.channel for item in notifications], ["site"])

    def test_empty_notify_roles_send_to_no_users(self):
        analyst = User(
            username="analyst",
            display_name="分析员",
            role="分析员",
            is_active=True,
        )
        rule = AlertRule(
            name="仪器故障后移",
            rule_type="instrument_fault_reschedule",
            enabled=True,
            enable_site=True,
            enable_wecom=False,
            notify_roles="[]",
        )
        self.db.add_all([analyst, rule])
        self.db.commit()

        sent = push_by_rule(
            self.db,
            "instrument_fault_reschedule",
            [analyst],
            "测试通知",
            "测试内容",
        )
        self.db.flush()

        self.assertEqual(sent, 0)
        self.assertEqual(self.db.query(Notification).count(), 0)

    def test_enabled_channels_deliver_in_background(self):
        analyst = User(
            username="analyst",
            display_name="分析员",
            role="分析员",
            wecom_id="wecom-analyst",
            is_active=True,
        )
        rule = AlertRule(
            name="排程变更",
            rule_type="schedule_changed",
            enabled=True,
            enable_site=True,
            enable_wecom=True,
            notify_roles='["分析员"]',
        )
        config = PushChannelConfig(
            wecom_enabled=True,
            wecom_corp_id="corp",
            wecom_agent_id="agent",
            wecom_secret="secret",
        )
        self.db.add_all([analyst, rule, config])
        self.db.commit()

        sent = push_by_rule(
            self.db,
            "schedule_changed",
            [analyst],
            "测试通知",
            "测试内容",
        )
        self.db.commit()
        pending_notification = self.db.query(Notification).filter(
            Notification.channel == "wecom",
        ).one()
        self.assertEqual("pending", pending_notification.delivery_status)

        with patch(
            "app.services.wecom_delivery_service._send_wecom_text",
            return_value=WeComSendResult(True),
        ):
            processed = _process_pending_wecom_notifications(self.db)

        notifications = self.db.query(Notification).order_by(Notification.id).all()
        self.assertEqual(1, sent)
        self.assertEqual(1, processed)
        self.assertEqual([item.channel for item in notifications], ["site", "wecom"])
        self.assertTrue(all(item.delivery_status == "success" for item in notifications))

    def test_missing_wecom_config_fails_in_background_not_foreground(self):
        analyst = User(
            username="analyst",
            display_name="分析员",
            role="分析员",
            wecom_id="wecom-analyst",
            is_active=True,
        )
        self.db.add(analyst)
        self.db.commit()

        sent = push_by_rule(
            self.db,
            "unconfigured_test",
            [analyst],
            "测试通知",
            "测试内容",
        )
        self.db.commit()

        pending_notification = self.db.query(Notification).filter(
            Notification.channel == "wecom",
        ).one()
        self.assertEqual(1, sent)
        self.assertEqual("pending", pending_notification.delivery_status)

        processed = _process_pending_wecom_notifications(self.db)

        self.db.refresh(pending_notification)
        self.assertEqual(1, processed)
        self.assertEqual("failed", pending_notification.delivery_status)
        self.assertIn("未配置完整", pending_notification.error_message)

    def test_channel_switches_and_external_delivery_control_created_notifications(self):
        user = User(username="recipient", display_name="接收人", role="技术员", is_active=True)
        rule = AlertRule(name="排程变更", rule_type="schedule_changed", notify_roles=None)
        self.db.add_all([user, rule])
        self.db.commit()

        for enabled, site, wecom, external in product((False, True), repeat=4):
            with self.subTest(enabled=enabled, site=site, wecom=wecom, external=external):
                rule.enabled = enabled
                rule.enable_site = site
                rule.enable_wecom = wecom
                self.db.commit()
                with patch("app.services.push_notification_service.enqueue_wecom_delivery") as enqueue:
                    sent = push_by_rule(
                        self.db, "schedule_changed", [user, user], "测试通知", "测试内容",
                        external_delivery=external,
                    )
                self.db.flush()
                expected = []
                if enabled and site:
                    expected.append("site")
                if enabled and wecom and external:
                    expected.append("wecom")
                rows = self.db.query(Notification).order_by(Notification.id).all()
                self.assertEqual(expected, [row.channel for row in rows])
                self.assertEqual(int(bool(expected)), sent)
                self.assertEqual(int("wecom" in expected), enqueue.call_count)
                self.db.query(Notification).delete(synchronize_session="fetch")
                self.db.commit()


if __name__ == "__main__":
    unittest.main()
