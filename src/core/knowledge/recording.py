from __future__ import annotations

from uuid import uuid4

from src.core.knowledge.models import KnowledgeChange, KnowledgeQuery
from src.core.memory import Event, Actor, Subject, Source
from src.core.principal import actor_in, actor_type_in
from src.core.services import memory_services
from src.utils.logger import logger


def event_for_change(scope, change):
    item = change.after or change.before
    return Event(
        id=uuid4().hex,
        type=change.type,
        source=Source.for_scope(scope),
        when=change.when,
        what=item.summary,
        actors=[change.actor],
        subjects=[
            Subject(id=item.id, type="knowledge.item", name=item.summary),
            *(
                Subject(id=f"path:{path}", type="code.path", name=path)
                for path in item.properties.get("paths", ())
                if not any(mark in path for mark in "*?[") and not path.endswith("/")
            ),
        ],
        attributes={
            "knowledge.status_before": change.before.status if change.before else None,
            "knowledge.status_after": change.after.status if change.after else None,
            "knowledge.summary_before": (
                change.before.summary if change.before else None
            ),
            "knowledge.summary_after": change.after.summary if change.after else None,
        },
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
        memory = (
            memory_services()
            if self.services is None
            else memory_services(self.services)
        )
        actor = actor_in(scope)
        change = KnowledgeChange(
            event_type,
            Actor(id=actor, name=actor, type=actor_type_in(scope), role="author"),
            before,
            after,
        )
        memory.record(scope, [event_for_change(scope, change)])


def recording(store, services=None):
    return (
        store
        if isinstance(store, RecordingKnowledge)
        else RecordingKnowledge(store, services)
    )
