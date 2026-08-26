from app.models import Notification


def mark_unread_site_notifications_read(db, username: str) -> int:
    return (
        db.query(Notification)
        .filter(
            Notification.user_name == username,
            Notification.channel == "site",
            Notification.is_read.is_(False),
        )
        .update({Notification.is_read: True}, synchronize_session=False)
    )
