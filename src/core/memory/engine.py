from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import httpx

from src.core.scope import Scope
from .models import Event, EventLink, EventPage, Actor, Subject, Source
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
class EventRecord:
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
    def from_dict(cls, body: dict) -> EventRecord:
        return cls(**body)


def compact(body: dict) -> dict:
    return {
        key: value for key, value in body.items() if value not in (None, "", [], {})
    }


def binding(scope: Scope) -> tuple[str, str, str]:
    source = Source.for_scope(scope)
    return source.system, source.installation, source.container


def _time(value) -> datetime:
    if isinstance(value, dict):
        value = value["at"]
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _instant(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _at(event: dict) -> datetime:
    return _time(event["occurred_at"])


def _title(attributes, place_attributes, content):
    return (
        attributes.get("title")
        or next(
            (
                value
                for key, value in place_attributes.items()
                if key.endswith(".title")
            ),
            None,
        )
        or (content.splitlines()[0] if content else "")
    )


def _ratings(record):
    return {
        name: next(
            (
                item[name]
                for item in reversed(record.get("appraisals", []))
                if name in item
            ),
            None,
        )
        for name in ("valence", "importance")
    }


def _validate(event):
    if event.type == "sourceant.observation" and not event.what.strip():
        raise ValueError("text must not be empty")
    for name in ("when", "ended_at"):
        value = getattr(event, name)
        if value is None:
            continue
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(f"{name} must be a timezone-aware datetime")
    if event.valence is not None and not -2 <= event.valence <= 2:
        raise ValueError("valence must be between -2 and 2")
    if event.importance is not None and not 1 <= event.importance <= 5:
        raise ValueError("importance must be between 1 and 5")
    for actor in event.actors:
        if actor.type not in {
            "person",
            "agent",
            "bot",
            "service",
            "group",
            "organisation",
        }:
            raise ValueError(
                "actor type must be person, agent, bot, service, group or organisation"
            )


def _validate_recall(text, limit):
    if not text.strip() or not 1 <= limit <= 100:
        raise ValueError("recall needs a query and a limit between 1 and 100")


@dataclass(frozen=True)
class _StoredView:
    record: dict
    entities: frozenset[str]
    entity_sources: dict[str, dict]


class _ScopeMemory:
    configured = True

    def record(self, scope: Scope, events: list[Event]) -> list[Event]:
        source_events = events
        for source_event in source_events:
            _validate(source_event)
            subjects = {subject.id for subject in source_event.subjects}
            for subject in source_event.subjects:
                if subject.parent and subject.parent not in subjects:
                    raise ValueError(
                        f"Subject {subject.id!r} has missing parent {subject.parent!r}"
                    )
        records, entities = {}, {}
        bucket = self.bucket(scope)
        scope_namespace = sha256(repr(binding(scope)).encode()).hexdigest()[:24]
        for source_event in source_events:
            source = source_event.source
            namespace = (
                source.id_namespace
                or sha256(
                    repr(
                        (source.system, source.installation, source.container)
                    ).encode()
                ).hexdigest()[:24]
            )
            namespace += "_" + scope_namespace
            prefix = namespace + "_"

            stored = getattr(source, "_stored_view", None)
            if (
                not isinstance(stored, _StoredView)
                or stored.record["bucket"] != bucket
                or stored.record["id"] != source_event.id
            ):
                stored = None
            references = (
                {stored.record.get(name) for name in ("id", "part_of", "in_reply_to")}
                if stored
                else set()
            )

            def event_id(value):
                return value if value in references else "ev_" + prefix + value

            def actor_id(value):
                return (
                    value
                    if stored and value in stored.entities
                    else "ac_" + prefix + value
                )

            def subject_id(subject):
                if stored and subject.id in stored.entities:
                    return subject.id
                if subject.type == "code.path":
                    return (
                        "sb_"
                        + sha256(
                            f"{bucket}\0path\0{subject.name}".encode()
                        ).hexdigest()[:24]
                    )
                if subject.role == "place":
                    return (
                        "pl_" + namespace
                        if subject.id == source.container
                        else "pl_" + prefix + subject.id
                    )
                return (
                    "sb_"
                    + sha256(
                        f"{bucket}\0{subject.type}\0{subject.id}".encode()
                    ).hexdigest()[:24]
                )

            subjects = {subject.id: subject for subject in source_event.subjects}
            for actor in source_event.actors:
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
            for subject in source_event.subjects:
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
                    source=(
                        stored.entity_sources.get(subject.id)
                        if stored
                        else (
                            compact(
                                {
                                    "system": source.system,
                                    "installation": source.installation,
                                    "native_id": source.container,
                                    "url": source.container_url,
                                }
                            )
                            if subject.id == source.container
                            else None
                        )
                    ),
                )
                entities[entity.id] = entity
            place = next(
                (
                    subject_id(subject)
                    for subject in source_event.subjects
                    if subject.role == "place"
                ),
                "",
            )

            if not place and not stored:
                place = "pl_" + namespace
                entities[place] = Entity(
                    bucket,
                    place,
                    f"{source.system}.container",
                    "place",
                    source.container,
                    url=source.container_url,
                    source=compact(
                        {
                            "system": source.system,
                            "installation": source.installation,
                            "native_id": source.container,
                            "url": source.container_url,
                        }
                    ),
                )

            def instant(value, original=None):
                if value is None:
                    return None
                if original is not None and _time(original) == value:
                    return deepcopy(original)
                return value.isoformat().replace("+00:00", "Z")

            content = []
            if source_event.what:
                content = [{"text": source_event.what}]
                if source_event.actors:
                    content[0]["speaker"] = actor_id(source_event.actors[0].id)
            attributes = dict(source_event.attributes)
            place_attributes = next(
                (
                    subject.attributes
                    for subject in source_event.subjects
                    if subject.role == "place"
                ),
                {},
            )
            if source_event.title and source_event.title != _title(
                attributes, place_attributes, source_event.what
            ):
                attributes["title"] = source_event.title
            ratings = {
                name: getattr(source_event, name)
                for name in ("valence", "importance")
                if getattr(source_event, name) is not None
            }
            appraisals = []
            if ratings:
                appraisals = [
                    {
                        "bucket": bucket,
                        "id": "ap_" + prefix + source_event.id,
                        "target": event_id(source_event.id),
                        "at": instant(source_event.when),
                        **ratings,
                    }
                ]
                if source_event.actors:
                    appraisals[0]["by"] = actor_id(source_event.actors[0].id)
            if stored and ratings == {
                name: value
                for name, value in _ratings(stored.record).items()
                if value is not None
            }:
                appraisals = deepcopy(stored.record.get("appraisals", []))
            event = EventRecord(
                bucket,
                event_id(source_event.id),
                source_event.type,
                occurred_at=instant(
                    source_event.when,
                    stored.record.get("occurred_at") if stored else None,
                ),
                ended_at=instant(
                    source_event.ended_at,
                    stored.record.get("ended_at") if stored else None,
                ),
                why=source_event.why,
                place=place,
                participants=[
                    {"actor": actor_id(actor.id), "role": actor.role}
                    for actor in source_event.actors
                ],
                content=content,
                subjects=[
                    {"subject": subject_id(subject), "role": subject.role}
                    for subject in source_event.subjects
                    if subject.role != "place"
                ],
                part_of=event_id(source_event.part_of) if source_event.part_of else "",
                in_reply_to=(
                    event_id(source_event.in_reply_to)
                    if source_event.in_reply_to
                    else ""
                ),
                attributes=attributes,
                appraisals=appraisals,
                source=(
                    deepcopy(stored.record.get("source"))
                    if stored
                    else compact(
                        {
                            "system": source.system,
                            "installation": source.installation,
                            "native_id": source.native_id or source_event.id,
                            "url": source.url,
                        }
                    )
                ),
            )
            records[event.id] = event
        if records:
            self._write_records(list(records.values()), list(entities.values()))
        entity_views = [entity.to_dict() for entity in entities.values()]
        return [self._view(event.to_dict(), entity_views) for event in records.values()]

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
                        id=actor["id"],
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
        known = {ref["subject"] for ref in references}
        for reference in references:
            subject = resolve(reference.get("subject"))
            if subject.get("parent") and subject["parent"] not in known:
                known.add(subject["parent"])
                references.append({"subject": subject["parent"], "role": "place"})
        subjects = []
        for reference in references:
            subject = resolve(reference.get("subject"))
            if subject:
                subjects.append(
                    Subject(
                        id=subject["id"],
                        type=subject.get("type", ""),
                        name=subject.get("name", ""),
                        url=subject.get("url", ""),
                        role=reference.get("role", ""),
                        parent=subject.get("parent") or None,
                        attributes=deepcopy(subject.get("attributes", {})),
                    )
                )
        content = "\n".join(c["text"] for c in event.get("content", []))
        attributes = deepcopy(event.get("attributes", {}))
        source = event.get("source") or {}
        ratings = _ratings(event)
        view = Event(
            id=event["id"],
            type=event["type"],
            when=_time(event["occurred_at"]) if event.get("occurred_at") else None,
            ended_at=_time(event["ended_at"]) if event.get("ended_at") else None,
            why=event.get("why", ""),
            what=content,
            title=_title(attributes, place.get("attributes", {}), content),
            part_of=event.get("part_of") or None,
            in_reply_to=event.get("in_reply_to") or None,
            source=(
                Source(
                    system=source.get("system", ""),
                    installation=source.get("installation", ""),
                    container=(repository.get("source") or {}).get("native_id", ""),
                    url=source.get("url", ""),
                    container_url=repository.get("url", ""),
                    native_id=source.get("native_id", ""),
                    id_namespace=repository.get("id", "")
                    .removeprefix("pl_")
                    .rsplit("_", 1)[0],
                )
            ),
            actors=actors,
            subjects=subjects,
            attributes=attributes,
            score=score,
            outside_window=outside_window,
            **ratings,
        )
        # Source retains provenance when callers replace read-only event fields.
        object.__setattr__(
            view.source,
            "_stored_view",
            _StoredView(
                deepcopy(event),
                frozenset(
                    [actor.id for actor in actors]
                    + [subject.id for subject in subjects]
                ),
                {
                    ref: deepcopy(entity["source"])
                    for ref, entity in resolved.items()
                    if entity.get("source")
                },
            ),
        )
        return view

    def recall(self, scope: Scope, text: str, limit: int = 10) -> list[Event]:
        _validate_recall(text, limit)
        result = self._recall(
            {
                "context": {"text": text},
                "filters": {"buckets": [self.bucket(scope)]},
                "limit": limit,
            }
        )
        events = []
        for item in result.get("results", []):
            event = item["record"]
            entities = result.get("entities", [])
            events.append(self._view(event, entities, item.get("score", 0.0)))
        return events

    def events(self, scope: Scope, since=None, until=None, after=None) -> EventPage:
        buckets = [self.bucket(scope)]
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
        views.sort(
            key=lambda event: (
                event.when or datetime.min.replace(tzinfo=timezone.utc),
                event.id,
            )
        )
        return EventPage(views, page.get("next"))

    def links(
        self,
        scope: Scope,
        since=None,
        until=None,
        *,
        approaches: list[str] | None = None,
        min_score: float = 0.0,
        limit: int = 500,
    ) -> list[EventLink]:
        now = datetime.now(timezone.utc)
        query = {
            "buckets": [self.bucket(scope)],
            "from": _instant(since) or "1970-01-01T00:00:00Z",
            "to": _instant(until) or (now + timedelta(days=1)).isoformat(),
            "limit": limit,
        }
        if approaches:
            query["approaches"] = approaches
        if min_score:
            query["min_score"] = min_score
        return [
            EventLink(
                source=link["from"],
                target=link["to"],
                score=link["score"],
                because=link.get("because") or {},
            )
            for link in self._links(query).get("links", [])
        ]
