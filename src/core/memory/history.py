from __future__ import annotations

from src.core.services import memory_services
from src.utils.logger import logger


def _stored(full_name: str) -> list:
    from sqlmodel import Session, select
    from src.config.db import get_engine
    from src.models.repository_event import RepositoryEvent

    engine = get_engine()
    if engine is None:
        return []
    with Session(engine) as session:
        return list(
            session.exec(
                select(RepositoryEvent)
                .where(
                    RepositoryEvent.repository_full_name == full_name,
                    RepositoryEvent.type == "pull_request",
                    RepositoryEvent.provider.in_(["github", "GitHub"]),
                )
                .order_by(RepositoryEvent.id)
            ).all()
        )


def record_webhook(
    event_type: str, payload: dict, *, stored: bool = True, strict: bool = False
) -> None:
    if not isinstance(payload.get("repository"), dict):
        return
    try:
        from .interfaces import RepositoryDelivery
        from src.core.scope import Scope
        from hashlib import sha256
        import json

        delivery_id = str(
            payload.get("sourceant_delivery_id")
            or sha256(
                json.dumps([event_type, payload], sort_keys=True).encode()
            ).hexdigest()
        )
        memory_services().record_repository_event(
            Scope.from_mapping({"repository": payload["repository"]["full_name"]}),
            RepositoryDelivery(delivery_id, event_type, payload, stored),
        )
    except Exception:
        logger.warning(
            "Could not record a %s webhook in memory", event_type, exc_info=True
        )
        if strict:
            raise


def replay(full_names: list[str] | None = None) -> int:
    from sqlmodel import Session, select
    from src.config.db import get_engine
    from src.models.repository_event import RepositoryEvent

    engine = get_engine()
    if engine is None:
        raise ValueError("Replaying repository events requires a database")
    with Session(engine) as session:
        query = select(RepositoryEvent).where(
            RepositoryEvent.provider.in_(["github", "GitHub"])
        )
        if full_names:
            query = query.where(RepositoryEvent.repository_full_name.in_(full_names))
        rows = list(session.exec(query.order_by(RepositoryEvent.id)).all())
    for row in rows:
        record_webhook(row.type, row.payload or {}, stored=True, strict=True)
    return len(rows)
