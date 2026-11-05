from __future__ import annotations

from .engine import Entity, EventRecord, _ScopeMemory, binding, _at, _time
from copy import deepcopy
from hashlib import sha256
from threading import RLock
from src.core.scope import Scope


class _InMemoryRecords:
    def __init__(self):
        self._events: dict[str, dict] = {}
        self.entities: dict[str, dict] = {}
        self._lock = RLock()

    def bind(self, system: str, installation: str, container: str) -> str:
        return (
            "bk_"
            + sha256(repr((system, installation, container)).encode()).hexdigest()[:24]
        )

    def bucket(self, scope: Scope) -> str:
        return self.bind(*binding(scope))

    def _write_records(self, events: list[EventRecord], entities: list[Entity]) -> None:
        with self._lock:
            pending = {}
            captures = {
                self._capture_identity(body): body
                for body in self._events.values()
                if body.get("source")
            }
            bodies = [event.to_dict() for event in events]
            aliases = {}
            for body in bodies:
                if body.get("source", {}).get("native_id"):
                    identity = self._capture_identity(body)
                    captured = captures.setdefault(identity, body)
                    aliases[body["id"]] = captured["id"]

            for body in bodies:
                for key in ("id", "part_of", "in_reply_to"):
                    if key in body:
                        body[key] = aliases.get(body[key], body[key])
                for appraisal in body.get("appraisals", []):
                    if "target" in appraisal:
                        appraisal["target"] = aliases.get(
                            appraisal["target"], appraisal["target"]
                        )
                previous = pending.get(body["id"], self._events.get(body["id"]))
                if previous is not None and previous != body:
                    raise ValueError("An event with this id has different content")
                pending[body["id"]] = body
            self._events.update(deepcopy(pending))
            self.entities.update(
                {entity.id: deepcopy(entity.to_dict()) for entity in entities}
            )

    @staticmethod
    def _capture_identity(body):
        source = body["source"]
        return (
            body["bucket"],
            source.get("system", ""),
            source.get("installation", ""),
            source.get("native_id", ""),
        )

    def list(
        self, scope: Scope, since=None, until=None, after=None, *, buckets=None
    ) -> dict:
        buckets = buckets if buckets is not None else [self.bucket(scope)]
        with self._lock:
            ordered = sorted(
                (
                    event
                    for event in self._events.values()
                    if event["bucket"] in buckets and event.get("occurred_at")
                ),
                key=lambda event: (_at(event), event["id"]),
            )
            cursor = self._events.get(after) if after else None
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
                for event in self._events.values()
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


class InMemoryMemory(_ScopeMemory, _InMemoryRecords):
    def entity(self, id):
        return Entity.from_dict(deepcopy(self.entities[id]))

    def event(self, id):
        return EventRecord.from_dict(deepcopy(self._events[id]))
