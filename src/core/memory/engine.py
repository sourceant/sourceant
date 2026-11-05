from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4
from .interfaces import (
    Remembered,
    RememberedEvent,
    RecallResult,
    HistoryPage,
    KnowledgeChange,
)

import httpx

from src.core.scope import Scope
from .models import MemoryEvent, Actor, Subject, Occurrence
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Entity:
    bucket: str
    id: str
    type: str
    entity_class: str = ""
    name: str = ""
    parent: str = ""
    url: str = ""
    audience: dict | None = None
    medium: dict | None = None
    image: str = ""
    identities: list[dict] = field(default_factory=list)
    member_of: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    profiles: list[str] = field(default_factory=list)
    source: dict | None = None

    def to_dict(self) -> dict:
        body = asdict(self)
        body["class"] = body.pop("entity_class")
        return compact(body)

    @classmethod
    def from_dict(cls, body: dict) -> Entity:
        body = dict(body)
        body["entity_class"] = body.pop("class", "")
        return cls(**body)


@dataclass(frozen=True)
class Event:
    bucket: str
    id: str
    type: str
    occurred_at: str | dict | None = None
    ended_at: str | dict | None = None
    why: str = ""
    recall: dict | None = None
    place: str = ""
    participants: list[dict] = field(default_factory=list)
    content: list[dict] = field(default_factory=list)
    subjects: list[dict] = field(default_factory=list)
    involves: list[dict] = field(default_factory=list)
    part_of: str = ""
    in_reply_to: str = ""
    appraisals: list[dict] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    profiles: list[str] = field(default_factory=list)
    source: dict | None = None

    def to_dict(self) -> dict:
        return compact(asdict(self))

    @classmethod
    def from_dict(cls, body: dict) -> Event:
        return cls(**body)


def compact(body: dict) -> dict:
    return {
        key: value for key, value in body.items() if value not in (None, "", [], {})
    }


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
    def record(self, scope: Scope, occurrences: list[Occurrence]) -> None:
        for occurrence in occurrences:
            subjects = {subject.id for subject in occurrence.subjects}
            for subject in occurrence.subjects:
                if subject.parent and subject.parent not in subjects:
                    raise ValueError(
                        f"Subject {subject.id!r} has missing parent {subject.parent!r}"
                    )
        events, entities = {}, {}
        for occurrence in occurrences:
            source = occurrence.source
            bucket = self.bind(source.system, source.installation, source.container)
            namespace = (
                source.id_namespace
                or sha256(
                    repr(
                        (source.system, source.installation, source.container)
                    ).encode()
                ).hexdigest()[:24]
            )
            prefix = namespace + "_"

            def event_id(value):
                return "ev_" + prefix + value

            def actor_id(value):
                return "ac_" + prefix + value

            def subject_id(subject):
                if subject.type == "code.path":
                    return (
                        "sb_"
                        + sha256(
                            f"{bucket}\0path\0{subject.name}".encode()
                        ).hexdigest()[:24]
                    )
                if subject.container_root:
                    return "pl_" + namespace
                if subject.place_id is not None:
                    return "pl_" + prefix + subject.place_id
                return (
                    "sb_"
                    + sha256(
                        f"{bucket}\0{subject.type}\0{subject.id}".encode()
                    ).hexdigest()[:24]
                )

            subjects = {subject.id: subject for subject in occurrence.subjects}
            for actor in occurrence.actors:
                entity = Entity(
                    bucket,
                    actor_id(actor.id),
                    actor.type,
                    "actor",
                    actor.name,
                    url=actor.url,
                    image=actor.image,
                )
                entities[entity.id] = entity
            for subject in occurrence.subjects:
                entity = Entity(
                    bucket,
                    subject_id(subject),
                    subject.type,
                    "place" if subject.role == "place" else "subject",
                    subject.name,
                    parent=(
                        subject_id(subjects[subject.parent]) if subject.parent else ""
                    ),
                    url=subject.url,
                    attributes=subject.attributes,
                )
                entities[entity.id] = entity
            place = next(
                (
                    subject_id(subject)
                    for subject in occurrence.subjects
                    if subject.role == "place"
                ),
                "",
            )

            if not place:
                place = "pl_" + prefix + "container"
                entities[place] = Entity(
                    bucket,
                    place,
                    f"{source.system}.container",
                    "place",
                    source.container,
                    url=source.container_url,
                )

            def instant(value):
                return value.isoformat() if isinstance(value, datetime) else value

            content = []
            if occurrence.what:
                content = [{"text": occurrence.what}]
                if occurrence.actors:
                    content[0]["speaker"] = actor_id(occurrence.actors[0].id)
            attributes = dict(occurrence.attributes)
            if occurrence.title:
                attributes["title"] = occurrence.title
            ratings = {
                name: getattr(occurrence, name)
                for name in ("valence", "importance")
                if getattr(occurrence, name) is not None
            }
            appraisals = []
            if ratings:
                appraisals = [
                    {
                        "bucket": bucket,
                        "id": "ap_" + prefix + occurrence.id,
                        "target": event_id(occurrence.id),
                        "at": instant(occurrence.when),
                        **ratings,
                    }
                ]
                if occurrence.actors:
                    appraisals[0]["by"] = actor_id(occurrence.actors[0].id)
            event = Event(
                bucket,
                event_id(occurrence.id),
                occurrence.type,
                occurred_at=instant(occurrence.when),
                ended_at=instant(occurrence.ended_at),
                why=occurrence.why,
                place=place,
                participants=[
                    {"actor": actor_id(actor.id), "role": actor.role}
                    for actor in occurrence.actors
                ],
                content=content,
                subjects=[
                    {"subject": subject_id(subject), "role": subject.role}
                    for subject in occurrence.subjects
                    if subject.role != "place"
                ],
                part_of=event_id(occurrence.part_of) if occurrence.part_of else "",
                in_reply_to=(
                    event_id(occurrence.in_reply_to) if occurrence.in_reply_to else ""
                ),
                attributes=attributes,
                appraisals=appraisals,
                source=compact(
                    {
                        "system": source.system,
                        "installation": source.installation,
                        "native_id": source.native_id or occurrence.id,
                        "url": source.url,
                    }
                ),
            )
            events[event.id] = event
        if events:
            self._source_bindings.remember(
                scope, [occurrence.source for occurrence in occurrences]
            )
            self._write_records(list(events.values()), list(entities.values()))

    def remember(self, scope: Scope, observation: Remembered) -> RememberedEvent:
        from src.core.knowledge.recording import actor_in

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
        self._write_records([event], entities)
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
                actors.append(
                    Actor(
                        name=actor.get("name", ""),
                        image=actor.get("image", ""),
                        url=actor.get("url", ""),
                        role=participant.get("role", ""),
                        type=(
                            "bot"
                            if actor.get("type") == "software.bot"
                            else actor.get("type", "")
                        ),
                    )
                )
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
                subjects.append(
                    Subject(
                        type=subject.get("type", ""),
                        name=subject.get("name", ""),
                        url=subject.get("url", ""),
                        role=reference.get("role", ""),
                        parent=parent.get("name") or None,
                    )
                )
        content = "\n".join(c["text"] for c in event.get("content", []))
        attributes = deepcopy(event.get("attributes", {}))
        source = event.get("source") or {}
        ratings = {
            name: next(
                (a[name] for a in reversed(event.get("appraisals", [])) if name in a),
                None,
            )
            for name in ("valence", "importance")
        }
        return MemoryEvent(
            id=event["id"],
            type=event["type"],
            when=_time(event["occurred_at"]) if event.get("occurred_at") else None,
            ended_at=_time(event["ended_at"]) if event.get("ended_at") else None,
            why=event.get("why", ""),
            what=content,
            title=attributes.get("title")
            or next(
                (
                    value
                    for key, value in place.get("attributes", {}).items()
                    if key.endswith(".title")
                ),
                None,
            )
            or (content.splitlines()[0] if content else ""),
            part_of=event.get("part_of") or None,
            source={
                "system": source.get("system", ""),
                "container": repository.get("name", ""),
                "url": source.get("url", ""),
                "container_url": repository.get("url", ""),
            },
            actors=actors,
            subjects=subjects,
            attributes=attributes,
            score=score,
            outside_window=outside_window,
            **ratings,
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
        buckets = self.buckets(scope)
        page = self.list(scope, since, until, after, buckets=buckets)
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
            if parent.bucket in buckets:
                views.append(
                    self._view(
                        parent.to_dict(),
                        page.get("entities", []),
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
        self._write_records(
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
