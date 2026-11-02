from __future__ import annotations

from threading import RLock

from src.core.services import memory_services, service_registry
from .interfaces import BucketBinder
from src.utils.logger import logger
from .github import Bound, translate
from .models import Entity, Event

_heads: dict[tuple[str, str], dict] = {}
_lock = RLock()


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


def _head(payload: dict) -> None:
    repository, pull = payload.get("repository"), payload.get("pull_request")
    if not isinstance(repository, dict) or not isinstance(pull, dict):
        return
    branch = (pull.get("head") or {}).get("ref")
    if branch:
        _heads[(str(repository.get("id") or repository.get("full_name")), branch)] = (
            pull
        )


def record_webhook(
    event_type: str, payload: dict, *, stored: bool = True, strict: bool = False
) -> None:
    if not isinstance(payload.get("repository"), dict):
        return
    try:
        recorder, _ = memory_services()
        repository = payload["repository"]
        installation = str(
            (payload.get("installation") or {}).get("id")
            or repository.get("owner", {}).get("id")
            or "local"
        )
        container = str(repository.get("id") or repository["full_name"])
        binder = service_registry.resolve(BucketBinder)
        bucket = binder.bind("github", installation, container)
        binder.repository_sources[repository["full_name"]] = [
            {
                "repository": {
                    "id": repository.get("id"),
                    "full_name": repository["full_name"],
                    "owner": {"id": (repository.get("owner") or {}).get("id")},
                },
                "installation": {"id": installation},
                "sourceant_workspace_id": payload.get("sourceant_workspace_id"),
            }
        ]
        with _lock:
            if event_type == "push" and stored:
                for row in _stored(repository["full_name"]):
                    _head(row.payload or {})
            _head(payload)
            batch = translate(
                Bound(bucket, installation, container),
                event_type,
                payload.get("action"),
                payload,
                lambda branch: _heads.get((container, branch)),
            )
        if batch:
            recorder.record(
                [Event.from_dict(event) for event in batch["events"]],
                [Entity.from_dict(entity) for entity in batch["entities"]],
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
    with _lock:
        for row in rows:
            _head(row.payload or {})
    for row in rows:
        record_webhook(row.type, row.payload or {}, stored=False, strict=True)
    return len(rows)
