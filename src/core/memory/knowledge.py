from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from src.core.knowledge import KnowledgeQuery
from src.core.services import memory_services
from .models import Entity, Event
from .interfaces import BucketBinder
from src.utils.logger import logger

_actor: ContextVar[str | None] = ContextVar("memory_actor", default=None)


@contextmanager
def acting_as(actor: str):
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


def actor_in(scope):
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    return (
        (token.subject if token else None)
        or _actor.get()
        or scope.get("actor")
        or scope.get("agent")
        or scope.get("user")
        or "local"
    )


class RecordingKnowledge:
    def __init__(self, store, services=None):
        self.store = store
        self.services = services
        for name in (
            "search",
            "get_relationships",
            "traverse",
            "put_relationship",
            "get_links",
            "knowledge_ids_for_paths",
            "put_link",
            "select",
            "remove_relationship",
        ):
            if hasattr(store, name):
                setattr(self, name, getattr(store, name))
        if hasattr(store, "remove"):
            self.remove = self._remove

    def __getattr__(self, name):
        return getattr(self.store, name)

    def _before(self, scope, id):
        result = self.store.search(KnowledgeQuery(scope, ids=frozenset({id}), limit=1))
        return result.items[0] if result.items else None

    def put(self, scope, knowledge):
        before = self._before(scope, knowledge.id)
        self.store.put(scope, knowledge)
        event_type = (
            "knowledge.captured"
            if before is None
            else (
                "knowledge.status_changed"
                if before.status != knowledge.status
                else "knowledge.updated"
            )
        )
        self._record(scope, event_type, before, knowledge)

    def _remove(self, scope, knowledge_id):
        before = self._before(scope, knowledge_id)
        self.store.remove(scope, knowledge_id)
        if before is not None:
            self._record(scope, "knowledge.removed", before, None)

    def _record(self, scope, event_type, before, after):
        try:
            self._write(scope, event_type, before, after)
        except Exception:
            logger.warning(
                "Could not record a %s event in memory", event_type, exc_info=True
            )

    def _write(self, scope, event_type, before, after):
        recorder, _ = (
            memory_services()
            if self.services is None
            else memory_services(self.services)
        )
        from src.core.services import service_registry

        registry = self.services if self.services is not None else service_registry
        bucket = registry.resolve(BucketBinder).bucket(scope)
        item = after or before
        subject = (
            "sb_" + sha256(f"{bucket}\0knowledge\0{item.id}".encode()).hexdigest()[:24]
        )
        actor = actor_in(scope)
        actor_id = "ac_" + sha256(f"{bucket}\0{actor}".encode()).hexdigest()[:24]
        recorder.record(
            [
                Event(
                    bucket=bucket,
                    id="ev_" + uuid4().hex,
                    type=event_type,
                    occurred_at=datetime.now(timezone.utc).isoformat(),
                    participants=[{"actor": actor_id, "role": "author"}],
                    content=[{"speaker": actor_id, "text": item.summary}],
                    subjects=[{"subject": subject, "role": "about"}],
                    attributes={
                        "knowledge.status_before": before.status if before else None,
                        "knowledge.status_after": after.status if after else None,
                        "knowledge.summary_before": before.summary if before else None,
                        "knowledge.summary_after": after.summary if after else None,
                    },
                )
            ],
            [
                Entity(bucket, subject, "knowledge.item", "subject", item.summary),
                Entity(
                    bucket,
                    actor_id,
                    "agent" if scope.get("agent") else "person",
                    "actor",
                    actor,
                ),
            ],
        )


def recording(store, services=None):
    return (
        store
        if isinstance(store, RecordingKnowledge)
        else RecordingKnowledge(store, services)
    )
