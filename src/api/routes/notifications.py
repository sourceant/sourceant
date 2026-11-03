from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlmodel import Session, select

from src.auth import get_current_user
from src.config.db import get_session
from src.core.notifications import Notification, NotificationRead, mark_read
from src.core.responses import success_response
from src.core.workspace import workspace_of
from src.utils.moments import utc

router = APIRouter()


def _user_id(user: dict) -> str:
    identifier = str(user.get("user_id") or "")
    if not identifier:
        raise HTTPException(status_code=403, detail="A user identity is required")
    return identifier


@router.get("")
def notifications(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    unread_only: bool = False,
    user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    workspace, user_id = workspace_of(user), _user_id(user)
    read_ids = select(NotificationRead.notification_id).where(
        NotificationRead.user_id == user_id
    )
    mine = select(Notification).where(Notification.workspace == workspace)
    unread = Notification.id.not_in(read_ids)
    total = session.exec(
        select(func.count())
        .select_from(Notification)
        .where(Notification.workspace == workspace, unread)
    ).one()
    if unread_only:
        mine = mine.where(unread)
    found = session.exec(
        mine.order_by(Notification.created_at.desc(), Notification.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    rows = found[:limit]
    read = set(
        session.exec(
            read_ids.where(
                NotificationRead.notification_id.in_([row.id for row in rows])
            )
        ).all()
    )
    return success_response(
        {
            "items": [
                {
                    **row.model_dump(exclude={"workspace"}),
                    "created_at": utc(row.created_at),
                    "last_seen_at": utc(row.last_seen_at),
                    "read": row.id in read,
                }
                for row in rows
            ],
            "unread_count": total,
            "has_more": len(found) > limit,
        }
    )


@router.post("/{identifier}/read")
def read_notification(
    identifier: str,
    user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    workspace, user_id = workspace_of(user), _user_id(user)
    row = session.get(Notification, identifier)
    if row is None or row.workspace != workspace:
        raise HTTPException(status_code=404, detail="No such notification")
    mark_read(session, identifier, user_id)
    return success_response({"id": identifier, "read": True})
