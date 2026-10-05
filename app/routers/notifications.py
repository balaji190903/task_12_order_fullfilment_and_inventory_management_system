from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.misc import NotificationOut
from app.services import notification_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    unread_only: bool = Query(False), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    items = notification_service.list_notifications(db, current_user.id, unread_only=unread_only)
    return [NotificationOut.model_validate(n) for n in items]


@router.patch("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    notification = notification_service.mark_read(db, current_user.id, notification_id)
    db.commit()
    return notification


@router.patch("/read-all")
def mark_all_read(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    count = notification_service.mark_all_read(db, current_user.id)
    db.commit()
    return {"marked_read": count}
