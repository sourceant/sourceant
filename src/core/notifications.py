from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import Column, String, Text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel

from src.config.db import get_engine
from src.utils.logger import logger


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: str = Field(primary_key=True, max_length=64)
    workspace: str = Field(index=True, max_length=255)
    type: str = Field(max_length=100)
    severity: str = Field(max_length=20)
    title: str = Field(max_length=255)
    message: str = Field(sa_column=Column(Text, nullable=False))
    action_url: str = Field(max_length=500, default="")
    action_label: str = Field(max_length=100, default="")
    occurrences: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationRead(SQLModel, table=True):
    __tablename__ = "notification_reads"

    notification_id: str = Field(foreign_key="notifications.id", primary_key=True)
    user_id: str = Field(sa_column=Column(String(255), primary_key=True))
    read_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationEmail(SQLModel, table=True):
    __tablename__ = "notification_emails"

    notification_id: str = Field(foreign_key="notifications.id", primary_key=True)
    lease: str = ""
    leased_until: datetime | None = None
    sent_at: datetime | None = None


def publish(
    *,
    workspace: str,
    type: str,
    title: str,
    message: str,
    severity: str = "error",
    source: str = "",
    action_url: str = "",
    action_label: str = "",
) -> str | None:
    if not workspace:
        return None
    if severity not in {"info", "warning", "error"}:
        raise ValueError("Unknown notification severity")
    if action_url and (not action_url.startswith("/dashboard/") or "\\" in action_url):
        raise ValueError("Notification actions must stay inside the dashboard")
    engine = get_engine()
    if engine is None:
        return None
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    identifier = sha256(f"{workspace}\0{type}\0{source}\0{hour}".encode()).hexdigest()
    with Session(engine) as session:
        session.add(
            Notification(
                id=identifier,
                workspace=str(workspace),
                type=type,
                severity=severity,
                title=title,
                message=message,
                action_url=action_url,
                action_label=action_label,
            )
        )
        session.add(NotificationEmail(notification_id=identifier))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            session.exec(
                update(Notification)
                .where(Notification.id == identifier)
                .values(
                    occurrences=Notification.occurrences + 1,
                    last_seen_at=datetime.utcnow(),
                )
            )
            session.commit()
    return identifier


def report_failure(error: Exception, *, workspace=None, repository=None, model=None):
    if not workspace:
        return
    said = str(error).lower()
    if "insufficient_balance_error" in said or "insufficient balance" in said:
        type = "model.credits_exhausted"
        title = "Model credits exhausted"
        message = (
            "A model service could not process a request because its credits ran out. "
            "If you use your own API key, add credits to that provider account. "
            "Otherwise, contact SourceAnt support. Then retry the interrupted work."
        )
        source = "model-provider"
    elif model:
        from litellm.exceptions import AuthenticationError

        if not isinstance(error, AuthenticationError):
            return
        type = "model.authentication_failed"
        title = "Model credentials need attention"
        message = (
            "The model provider rejected the configured credentials. "
            "Check your API key and model access in Settings, then retry."
        )
        source = model
    else:
        type = "review.failed"
        title = "A review could not finish"
        message = "Open Reviews to check the interrupted review and retry it."
        source = repository or "review"
    try:
        publish(
            workspace=str(workspace),
            type=type,
            title=title,
            message=message,
            source=source,
            action_url=(
                "/dashboard/reviews"
                if type == "review.failed"
                else "/dashboard/settings"
            ),
            action_label=(
                "Open Reviews" if type == "review.failed" else "Open Settings"
            ),
        )
    except Exception:
        logger.warning("Could not persist a failure notification", exc_info=True)


def mark_read(session: Session, identifier: str, user_id: str) -> None:
    if session.get(NotificationRead, (identifier, user_id)) is not None:
        return
    session.add(NotificationRead(notification_id=identifier, user_id=user_id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
