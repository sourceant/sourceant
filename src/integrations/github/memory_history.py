from __future__ import annotations

from src.core.services import memory_services
from src.utils.logger import logger
from dataclasses import dataclass, field
from collections import OrderedDict
from threading import RLock
from hashlib import sha256
import json

MAX_ADAPTERS = 8
MAX_DELIVERIES = 4096
MAX_HEADS = 1024


class _LRU(OrderedDict):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.limit:
            self.popitem(last=False)

    def get(self, key, default=None):
        try:
            value = super().__getitem__(key)
        except KeyError:
            return default
        self.move_to_end(key)
        return value


@dataclass
class _Ingestion:
    deliveries: _LRU = field(default_factory=lambda: _LRU(MAX_DELIVERIES))
    heads: _LRU = field(default_factory=lambda: _LRU(MAX_HEADS))
    lock: object = field(default_factory=RLock)


_ingestions = _LRU(MAX_ADAPTERS)
_ingestions_lock = RLock()


def ingestion(memory):
    with _ingestions_lock:
        identity = id(memory)
        entry = _ingestions.get(identity)
        if entry is None:
            entry = (memory, _Ingestion())
            _ingestions[identity] = entry
        return entry[1]


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
        from src.core.scope import Scope
        from .memory_source import Origin, translate

        memory = memory_services()
        repository = payload["repository"]
        installation = str(
            (payload.get("installation") or {}).get("id")
            or (repository.get("owner") or {}).get("id")
            or "local"
        )
        container = str(repository.get("id") or repository["full_name"])
        state = ingestion(memory)
        heads = state.heads

        def head(item, wanted=None):
            pull = item.get("pull_request")
            if isinstance(pull, dict):
                branch = (pull.get("head") or {}).get("ref")
                if branch and (wanted is None or branch == wanted):
                    heads[(container, branch)] = pull

        delivery_id = str(
            payload.get("sourceant_delivery_id")
            or sha256(
                json.dumps([event_type, payload], sort_keys=True).encode()
            ).hexdigest()
        )
        with state.lock:
            if delivery_id in state.deliveries:
                state.deliveries.move_to_end(delivery_id)
                return
            if event_type == "push" and stored:
                branch = str(payload.get("ref") or "").removeprefix("refs/heads/")
                for row in _stored(repository["full_name"]):
                    head(row.payload or {}, wanted=branch)
            head(payload)
            occurrences = translate(
                Origin(installation, container),
                event_type,
                payload.get("action"),
                payload,
                lambda branch: heads.get((container, branch)),
            )
            if occurrences:
                memory.record(
                    Scope.from_mapping(
                        {
                            "repository": repository["full_name"],
                            **(
                                {"workspace": str(payload["sourceant_workspace_id"])}
                                if payload.get("sourceant_workspace_id")
                                else {}
                            ),
                        }
                    ),
                    occurrences,
                )
            state.deliveries[delivery_id] = None
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


def resolve_sources(scope):
    from src.core.memory.models import Source

    repository = scope.get("repository")
    if not repository:
        return []
    sources = []
    try:
        rows = _stored(repository)
    except Exception:
        return []
    for row in rows:
        payload = row.payload or {}
        if scope.get("workspace") and str(
            payload.get("sourceant_workspace_id")
        ) != scope.get("workspace"):
            continue
        container = payload.get("repository")
        if not isinstance(container, dict):
            continue
        sources.append(
            Source(
                system="github",
                installation=str(
                    (payload.get("installation") or {}).get("id")
                    or (container.get("owner") or {}).get("id")
                    or "local"
                ),
                container=str(container.get("id") or container.get("full_name")),
            )
        )
    return sources
