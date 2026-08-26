from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.models import Notification, User
from app.schemas.schemas import NotificationOut, NotificationReadAllOut
from app.services.notification_service import mark_all_site_notifications_read
from app.services.task_action_reminder_service import (
    start_task_action_reminder_worker,
    stop_task_action_reminder_worker,
)
from app.api.access import require_management_user, require_notification_owner
from app.api.users import require_authenticated_user

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["notifications"],
    on_startup=[start_task_action_reminder_worker],
    on_shutdown=[stop_task_action_reminder_worker],
)

@router.get("/history", response_model=List[NotificationOut])
def list_notification_history(
    limit: int = 200,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    safe_limit = min(max(limit, 1), 500)
    return (
        db.query(Notification)
        .order_by(Notification.created_at.desc())
        .limit(safe_limit)
        .all()
    )

@router.get("", response_model=List[NotificationOut])
def list_notifications(
    channel: Optional[str] = None,
    unread_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    query = db.query(Notification).filter(Notification.user_name == user.username)
    if channel:
        query = query.filter(Notification.channel == channel)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    return query.order_by(Notification.created_at.desc()).limit(50).all()


@router.put("/read-all", response_model=NotificationReadAllOut)
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    updated_count = mark_all_site_notifications_read(db, user.username)
    return {"status": "ok", "updated_count": updated_count}

@router.put("/{nid}/read")
def mark_read(
    nid: int,
    db: Session = Depends(get_db),
    _user=Depends(require_notification_owner),
):
    n = db.query(Notification).filter(Notification.id == nid).first()
    if n:
        n.is_read = True
        db.commit()
    return {"status": "ok"}

@router.post("/{nid}/confirm")
def confirm(
    nid: int,
    db: Session = Depends(get_db),
    _user=Depends(require_notification_owner),
):
    n = db.query(Notification).filter(Notification.id == nid).first()
    if n:
        n.is_confirmed = True
        db.commit()
    return {"status": "ok"}
