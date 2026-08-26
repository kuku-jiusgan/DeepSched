import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Notification
from app.services.notification_service import mark_all_site_notifications_read


class NotificationServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_mark_all_site_notifications_read_only_updates_current_user_unread_site_messages(self):
        current_unread = Notification(user_name="current", n_type="test", channel="site")
        current_read = Notification(user_name="current", n_type="test", channel="site", is_read=True)
        other_channel = Notification(user_name="current", n_type="test", channel="wecom")
        other_user = Notification(user_name="other", n_type="test", channel="site")
        self.db.add_all([current_unread, current_read, other_channel, other_user])
        self.db.commit()

        updated_count = mark_all_site_notifications_read(self.db, "current")

        self.assertEqual(1, updated_count)
        self.assertTrue(current_unread.is_read)
        self.assertTrue(current_read.is_read)
        self.assertFalse(other_channel.is_read)
        self.assertFalse(other_user.is_read)


if __name__ == "__main__":
    unittest.main()
