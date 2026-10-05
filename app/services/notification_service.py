from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import Notification
from app.exceptions import NotFoundError


def create_notification(
    db: Session,
    *,
    user_id: int,
    event_type: str,
    title: str,
    message: str,
    meta: dict | None = None,
) -> Notification:
    """Creates a notification row within the caller's transaction. In this
    reference implementation notifications are persisted synchronously (so
    they are consistent with the business change that triggered them); actual
    delivery (email/push/websocket) would be dispatched via FastAPI
    BackgroundTasks/Celery after commit - see routers for where tasks are queued.
    """
    notification = Notification(
        user_id=user_id,
        event_type=event_type,
        title=title,
        message=message,
        meta=meta,
    )
    db.add(notification)
    db.flush()
    return notification


def list_notifications(db: Session, user_id: int, unread_only: bool = False) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def mark_read(db: Session, user_id: int, notification_id: int) -> Notification:
    notification = db.get(Notification, notification_id)
    if not notification or notification.user_id != user_id:
        raise NotFoundError("Notification not found")
    notification.is_read = True
    db.flush()
    return notification


def mark_all_read(db: Session, user_id: int) -> int:
    notifications = list_notifications(db, user_id, unread_only=True)
    for n in notifications:
        n.is_read = True
    db.flush()
    return len(notifications)
