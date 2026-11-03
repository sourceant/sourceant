from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from threading import RLock
from urllib.parse import quote
from uuid import uuid4
from .interfaces import (
    Remembered,
    RememberedEvent,
    RecallResult,
    HistoryPage,
    KnowledgeChange,
    RepositoryDelivery,
)

import httpx

from src.core.scope import Scope
from .models import Entity, Event, MemoryEvent, Actor, Subject


def binding(scope: Scope) -> tuple[str, str, str]:
    return (
        "sourceant",
        scope.get("workspace", "local"),
        repr(
            tuple(
                (key, value)
                for key, value in scope.values
                if key not in {"actor", "principal", "user", "agent"}
            )
        ),
    )


class _EngineRecords:
    def __init__(
        self,
        url: str,
        key: str,
        *,
        person: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {"Authorization": f"Bearer {key}"}
        if person:
            headers["Memory-Actor"] = person
        self.client = httpx.Client(
            base_url=url.rstrip("/") + "/",
            headers=headers,
            transport=transport,
            timeout=5,
        )
        self._buckets: dict[tuple[str, str, str], str] = {}
        self.repository_sources: dict[str, list[dict]] = {}
        self._lock = RLock()

    def _request(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def bucket(self, scope: Scope) -> str:
        return self.bind(*binding(scope))

    def buckets(self, scope: Scope) -> list[str]:
        from .scopes import repository_buckets

        return repository_buckets(scope, self)

    def bind(self, system: str, installation: str, container: str) -> str:
        key = (system, installation, container)
        with self._lock:
            if key not in self._buckets:
                response = self._request(
                    "PUT",
                    "v1/buckets",
                    json={
                        "name": container,
                        "binding": dict(
                            zip(("system", "installation", "container"), key)
                        ),
                    },
                )
                self._buckets[key] = response["id"]
            return self._buckets[key]

    def record(self, events: list[Event], entities: list[Entity]) -> None:
        self._request(
            "POST",
            "v1/records",
            json={
                "events": [event.to_dict() for event in events],
                "entities": [entity.to_dict() for entity in entities],
            },
        )

    def list(self, scope: Scope, since=None, until=None, after=None) -> dict:
        params = {
            "bucket": self.buckets(scope),
            "from": since,
            "to": until,
            "after": after,
        }
        return self._request(
            "GET",
            "v1/events",
            params={key: value for key, value in params.items() if value is not None},
        )

    def _recall(self, query: dict) -> dict:
        return self._request("POST", "v1/recall", json=query)

    def event(self, id: str) -> Event:
        return Event.from_dict(self._request("GET", "v1/events/" + quote(id, safe="")))

    def entity(self, id: str) -> Entity:
        return Entity.from_dict(
            self._request("GET", "v1/entities/" + quote(id, safe=""))
        )

    def use(self, ids: list[str]) -> None:
        self._request("POST", "v1/events/use", json={"events": ids})

    def close(self) -> None:
        self.client.close()


class _InMemoryRecords:
    def __init__(self):
        self.repository_sources: dict[str, list[dict]] = {}
        self.events: dict[str, dict] = {}
        self.entities: dict[str, dict] = {}
        self._lock = RLock()

    def bind(self, system: str, installation: str, container: str) -> str:
        return (
            "bk_"
            + sha256(repr((system, installation, container)).encode()).hexdigest()[:24]
        )

    def bucket(self, scope: Scope) -> str:
        return self.bind(*binding(scope))

    def buckets(self, scope: Scope) -> list[str]:
        from .scopes import repository_buckets

        return repository_buckets(scope, self)

    def record(self, events: list[Event], entities: list[Entity]) -> None:
        with self._lock:
            pending = {}
            for event in events:
                body = event.to_dict()
                previous = pending.get(event.id, self.events.get(event.id))
                if previous is not None and previous != body:
                    raise ValueError("An event with this id has different content")
                pending[event.id] = body
            self.events.update(deepcopy(pending))
            self.entities.update(
                {entity.id: deepcopy(entity.to_dict()) for entity in entities}
            )

    def list(self, scope: Scope, since=None, until=None, after=None) -> dict:
        buckets = self.buckets(scope)
        with self._lock:
            ordered = sorted(
                (
                    event
                    for event in self.events.values()
                    if event["bucket"] in buckets and event.get("occurred_at")
                ),
                key=lambda event: (_at(event), event["id"]),
            )
            cursor = self.events.get(after) if after else None
            if after and (cursor is None or cursor["bucket"] not in buckets):
                return {"events": [], "entities": [], "next": None}
            events = [
                event
                for event in ordered
                if (
                    not cursor
                    or (_at(event), event["id"]) > (_at(cursor), cursor["id"])
                )
                and (not since or _at(event) >= _time(since))
                and (not until or _at(event) < _time(until))
            ][:200]
            wanted = {
                ref
                for event in events
                for ref in [
                    event.get("place"),
                    *(p["actor"] for p in event.get("participants", [])),
                    *(s["subject"] for s in event.get("subjects", [])),
                    *(i["entity"] for i in event.get("involves", [])),
                    *(c.get("speaker") for c in event.get("content", [])),
                ]
                if ref
            }
            for _ in range(2):
                wanted.update(
                    self.entities[ref]["parent"]
                    for ref in list(wanted)
                    if ref in self.entities and self.entities[ref].get("parent")
                )
            entities = [
                entity
                for id, entity in self.entities.items()
                if id in wanted and entity["bucket"] in buckets
            ]
            return deepcopy(
                {
                    "events": events,
                    "entities": entities,
                    "next": events[-1]["id"] if len(events) == 200 else None,
                }
            )

    def _recall(self, query: dict) -> dict:
        filters = query.get("filters", {})
        buckets = filters.get("buckets", [])
        text = query.get("context", {}).get("text", "").casefold()
        with self._lock:
            events = [
                deepcopy(event)
                for event in self.events.values()
                if (not buckets or event["bucket"] in buckets)
                and (
                    not text
                    or text
                    in " ".join(
                        item["text"] for item in event.get("content", [])
                    ).casefold()
                )
            ]
        return {
            "results": [
                {
                    "type": "event",
                    "id": event["id"],
                    "record": event,
                    "score": 1.0,
                }
                for event in events[: query.get("limit", 10)]
            ]
        }


def _time(value) -> datetime:
    if isinstance(value, dict):
        value = value["at"]
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _at(event: dict) -> datetime:
    return _time(event["occurred_at"])


def _actors(event):
    speakers = [
        item["speaker"] for item in event.get("content", []) if item.get("speaker")
    ]
    return list(
        dict.fromkeys(speakers or [p["actor"] for p in event.get("participants", [])])
    )


def _validate(observation):
    if not observation.what.strip():
        raise ValueError("text must not be empty")
    if observation.when.tzinfo is None or observation.when.utcoffset() is None:
        raise ValueError("when must include a timezone")
    if observation.valence is not None and not -2 <= observation.valence <= 2:
        raise ValueError("valence must be between -2 and 2")
    if observation.importance is not None and not 1 <= observation.importance <= 5:
        raise ValueError("importance must be between 1 and 5")


def _validate_recall(text, limit):
    if not text.strip() or not 1 <= limit <= 100:
        raise ValueError("recall needs a query and a limit between 1 and 100")


class _ScopeMemory:
    def remember(self, scope: Scope, observation: Remembered) -> RememberedEvent:
        from .knowledge import actor_in

        _validate(observation)
        bucket = self.bucket(scope)
        actor = observation.who or actor_in(scope)
        actor_id = "ac_" + sha256(f"{bucket}\0{actor}".encode()).hexdigest()[:24]
        entities = [Entity(bucket, actor_id, "person", "actor", actor)]
        subjects = []
        for path in observation.paths:
            subject = (
                "sb_" + sha256(f"{bucket}\0path\0{path}".encode()).hexdigest()[:24]
            )
            entities.append(Entity(bucket, subject, "code.path", "subject", path))
            subjects.append({"subject": subject, "role": "about"})
        event_id = "ev_" + uuid4().hex
        appraisals = []
        when = observation.when.isoformat()
        if observation.valence is not None or observation.importance is not None:
            appraisal = {
                "bucket": bucket,
                "id": "ap_" + uuid4().hex,
                "target": event_id,
                "by": actor_id,
                "at": when,
            }
            for name in ("valence", "importance"):
                value = getattr(observation, name)
                if value is not None:
                    appraisal[name] = value
            appraisals.append(appraisal)
        event = Event(
            bucket,
            event_id,
            observation.type,
            occurred_at=when,
            why=observation.why,
            participants=[{"actor": actor_id, "role": "author"}],
            content=[{"speaker": actor_id, "text": observation.what}],
            subjects=subjects,
            appraisals=appraisals,
        )
        self.record([event], entities)
        return RememberedEvent(
            event_id,
            observation.when,
            observation.why,
            actor,
            observation.what,
            observation.type,
        )

    def _view(self, event, entities, score=0.0, outside_window=False):
        resolved = {entity["id"]: entity for entity in entities}

        def resolve(ref):
            if not ref:
                return {}
            if ref not in resolved:
                try:
                    resolved[ref] = self.entity(ref).to_dict()
                except (KeyError, httpx.HTTPStatusError) as error:
                    if (
                        isinstance(error, httpx.HTTPStatusError)
                        and error.response.status_code != 404
                    ):
                        raise
                    resolved[ref] = {}
            return resolved[ref]

        actors = []
        for participant in event.get("participants", []):
            actor = resolve(participant.get("actor"))
            if actor:
                actors.append(Actor(
                    name=actor.get("name", ""), image=actor.get("image", ""),
                    url=actor.get("url", ""), role=participant.get("role", ""),
                    type="bot" if actor.get("type") == "software.bot" else actor.get("type", ""),
                ))
        references = list(event.get("subjects", []))
        if event.get("place"):
            references.append({"subject": event["place"], "role": "place"})
        place = resolve(event.get("place"))
        repository = place
        seen = set()
        while repository.get("parent") and repository["parent"] not in seen:
            seen.add(repository["parent"])
            repository = resolve(repository["parent"])
        subjects = []
        for reference in references:
            subject = resolve(reference.get("subject"))
            if subject:
                parent = resolve(subject.get("parent"))
                subjects.append(Subject(
                    type=subject.get("type", ""), name=subject.get("name", ""),
                    url=subject.get("url", ""), role=reference.get("role", ""),
                    parent=parent.get("name") or None,
                ))
        content = "\n".join(c["text"] for c in event.get("content", []))
        attributes = deepcopy(event.get("attributes", {}))
        source = event.get("source") or {}
        ratings = {
            name: next((a[name] for a in reversed(event.get("appraisals", [])) if name in a), None)
            for name in ("valence", "importance")
        }
        return MemoryEvent(
            id=event["id"], type=event["type"],
            when=_time(event["occurred_at"]) if event.get("occurred_at") else None,
            ended_at=_time(event["ended_at"]) if event.get("ended_at") else None,
            why=event.get("why", ""), what=content,
            title=attributes.get("title") or place.get("attributes", {}).get("github.title") or (content.splitlines()[0] if content else ""),
            part_of=event.get("part_of") or None,
            source={"system": source.get("system", ""),
                    "container": repository.get("name", ""),
                    "url": source.get("url", ""),
                    "container_url": repository.get("url", "")},
            actors=actors, subjects=subjects, attributes=attributes,
            score=score, outside_window=outside_window, **ratings,
        )

    def recall(self, scope: Scope, text: str, limit: int = 10) -> RecallResult:
        _validate_recall(text, limit)
        result = self._recall(
            {
                "context": {"text": text},
                "filters": {"buckets": self.buckets(scope)},
                "limit": limit,
            }
        )
        events = []
        for item in result.get("results", []):
            event = item["record"]
            entities = result.get("entities", [])
            events.append(self._view(event, entities, item.get("score", 0.0)))
        return RecallResult(events)

    def history(self, scope: Scope, since=None, until=None, after=None) -> HistoryPage:
        page = self.list(scope, since, until, after)
        records = list(page["events"])
        ids = {event["id"] for event in records}
        views = [self._view(event, page.get("entities", [])) for event in records]
        for event in records:
            parent_id = event.get("part_of")
            if not parent_id or parent_id in ids:
                continue
            ids.add(parent_id)
            try:
                parent = self.event(parent_id)
            except (KeyError, httpx.HTTPStatusError) as error:
                if (
                    isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code != 404
                ):
                    raise
                continue
            if parent.bucket in self.buckets(scope):
                views.append(
                    self._view(
                        parent.to_dict(), page.get("entities", []),
                        outside_window=True,
                    )
                )
        return HistoryPage(views, page.get("next"))

    def record_knowledge_change(self, scope: Scope, change: KnowledgeChange) -> None:
        before, after, event_type = change.before, change.after, change.type
        bucket = self.bucket(scope)
        item = after or before
        subject = (
            "sb_" + sha256(f"{bucket}\0knowledge\0{item.id}".encode()).hexdigest()[:24]
        )
        actor = change.actor
        actor_id = "ac_" + sha256(f"{bucket}\0{actor}".encode()).hexdigest()[:24]
        self.record(
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

    def _head(self, payload):
        repository, pull = payload.get("repository"), payload.get("pull_request")
        if isinstance(repository, dict) and isinstance(pull, dict):
            branch = (pull.get("head") or {}).get("ref")
            if branch:
                self._heads[
                    (str(repository.get("id") or repository.get("full_name")), branch)
                ] = pull

    def record_repository_event(
        self, scope: Scope, delivery: RepositoryDelivery
    ) -> None:
        from .github import Bound, translate
        from .history import _stored

        with self._delivery_lock:
            if delivery.id in self._deliveries:
                return
            self._record_delivery(delivery)
            self._deliveries.add(delivery.id)

    def _record_delivery(self, delivery):
        from .github import Bound, translate
        from .history import _stored

        event_type, payload, stored = delivery.type, delivery.payload, delivery.stored
        repository = payload["repository"]
        installation = str(
            (payload.get("installation") or {}).get("id")
            or repository.get("owner", {}).get("id")
            or "local"
        )
        container = str(repository.get("id") or repository["full_name"])
        binder = self
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
        with self._delivery_lock:
            if event_type == "push" and stored:
                for row in _stored(repository["full_name"]):
                    self._head(row.payload or {})
            self._head(payload)
            batch = translate(
                Bound(bucket, installation, container),
                event_type,
                payload.get("action"),
                payload,
                lambda branch: self._heads.get((container, branch)),
            )
        if batch:
            self.record(
                [Event.from_dict(event) for event in batch["events"]],
                [Entity.from_dict(entity) for entity in batch["entities"]],
            )


class TrailpadMemory(_ScopeMemory, _EngineRecords):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._delivery_lock = RLock()
        self._deliveries = set()
        self._heads = {}


class InMemoryMemory(_ScopeMemory, _InMemoryRecords):
    def __init__(self):
        super().__init__()
        self._delivery_lock = RLock()
        self._deliveries = set()
        self._heads = {}

    def entity(self, id):
        return Entity.from_dict(deepcopy(self.entities[id]))

    def event(self, id):
        return Event.from_dict(deepcopy(self.events[id]))


class DisabledMemory:
    def remember(self, scope, observation):
        _validate(observation)
        return RememberedEvent(
            "",
            observation.when,
            observation.why,
            observation.who,
            observation.what,
            observation.type,
            configured=False,
        )

    def recall(self, scope, text, limit=10):
        _validate_recall(text, limit)
        return RecallResult(configured=False)

    def history(self, scope, since=None, until=None, after=None):
        return HistoryPage(configured=False)

    def record_knowledge_change(self, scope, change):
        pass

    def record_repository_event(self, scope, delivery):
        pass
