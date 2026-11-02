from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from threading import RLock
from urllib.parse import quote

import httpx

from src.core.scope import Scope
from .models import Entity, Event


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


class EngineMemory:
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
            headers["Memory-Person"] = person
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

    def recall(self, query: dict) -> dict:
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


class InMemoryMemory:
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

    def recall(self, query: dict) -> dict:
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
                {"kind": "event", "id": event["id"], "record": event, "score": 1.0}
                for event in events[: query.get("limit", 10)]
            ]
        }


def _time(value) -> datetime:
    if isinstance(value, dict):
        value = value["at"]
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _at(event: dict) -> datetime:
    return _time(event["occurred_at"])
