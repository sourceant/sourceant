from __future__ import annotations

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
