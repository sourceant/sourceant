from datetime import datetime, timedelta
from secrets import token_urlsafe

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, update
from sqlmodel import Session, select

from src.auth import decode_access_token
from src.config.db import get_session
from src.core.notifications import Notification, NotificationEmail
from src.core.responses import success_response


async def gateway(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        decode_access_token(authorization[7:], audience="sourceant:notifications")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


router = APIRouter(dependencies=[Depends(gateway)])


class Lease(BaseModel):
    lease: str


def available():
    return (
        NotificationEmail.sent_at.is_(None),
        or_(
            NotificationEmail.leased_until.is_(None),
            NotificationEmail.leased_until < datetime.utcnow(),
        ),
    )


@router.get("/pending")
def pending(
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_session),
):
    identifiers = session.exec(
        select(NotificationEmail.notification_id)
        .join(Notification, Notification.id == NotificationEmail.notification_id)
        .where(*available())
        .order_by(Notification.created_at)
        .limit(limit)
    ).all()
    return success_response(list(identifiers))


@router.post("/{identifier}/claim")
def claim(identifier: str, session: Session = Depends(get_session)):
    lease = token_urlsafe(24)
    result = session.exec(
        update(NotificationEmail)
        .where(NotificationEmail.notification_id == identifier, *available())
        .values(lease=lease, leased_until=datetime.utcnow() + timedelta(minutes=15))
    )
    session.commit()
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409, detail="Notification email is not available"
        )
    row = session.get(Notification, identifier)
    return success_response({**row.model_dump(mode="json"), "lease": lease})


@router.post("/{identifier}/sent")
def sent(identifier: str, body: Lease, session: Session = Depends(get_session)):
    result = session.exec(
        update(NotificationEmail)
        .where(
            NotificationEmail.notification_id == identifier,
            NotificationEmail.lease == body.lease,
        )
        .values(sent_at=datetime.utcnow())
    )
    session.commit()
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Notification email lease changed")
    return success_response({"sent": True})


@router.post("/{identifier}/release")
def release(identifier: str, body: Lease, session: Session = Depends(get_session)):
    session.exec(
        update(NotificationEmail)
        .where(
            NotificationEmail.notification_id == identifier,
            NotificationEmail.lease == body.lease,
            NotificationEmail.sent_at.is_(None),
        )
        .values(leased_until=None)
    )
    session.commit()
    return success_response({"released": True})
