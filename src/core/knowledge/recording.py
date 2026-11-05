from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from src.core.knowledge import KnowledgeQuery
from src.core.services import memory_services
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
        from src.core.memory.interfaces import KnowledgeChange

        memory = (
            memory_services()
            if self.services is None
            else memory_services(self.services)
        )
        memory.record_knowledge_change(
            scope, KnowledgeChange(event_type, actor_in(scope), before, after)
        )


def recording(store, services=None):
    return (
        store
        if isinstance(store, RecordingKnowledge)
        else RecordingKnowledge(store, services)
    )
