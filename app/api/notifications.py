import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from app.deps import CurrentUser, DbSession
from app.models import Notification
from app.schemas.work import NotificationOut

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    principal: CurrentUser, session: DbSession, unread: bool = False, limit: int = Query(30, ge=1, le=100)
):
    query = sa.select(Notification).where(Notification.user_id == principal.id)
    if unread:
        query = query.where(Notification.read_at.is_(None))
    return list(await session.scalars(query.order_by(Notification.created_at.desc()).limit(limit)))


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: uuid.UUID, principal: CurrentUser, session: DbSession) -> None:
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.user_id != principal.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    notification.read_at = notification.read_at or datetime.now(UTC)
    await session.commit()
