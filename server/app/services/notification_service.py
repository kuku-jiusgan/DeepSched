from app.repositories.notification_repository import mark_unread_site_notifications_read


def mark_all_site_notifications_read(db, username: str) -> int:
    updated_count = mark_unread_site_notifications_read(db, username)
    db.commit()
    return updated_count
