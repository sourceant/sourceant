from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from datetime import datetime


@dataclass(frozen=True)
class Actor:
    name: str = ""
    image: str = ""
    url: str = ""
    role: str = ""
    type: str = ""


@dataclass(frozen=True)
class Subject:
    type: str = ""
    name: str = ""
    url: str = ""
    role: str = ""
    parent: str | None = None


@dataclass(frozen=True)
class MemoryEvent:
    id: str
    type: str
    when: datetime | None = None
    ended_at: datetime | None = None
    why: str = ""
    what: str = ""
    title: str = ""
    part_of: str | None = None
    source: dict[str, str] = field(default_factory=dict)
    actors: list[Actor] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    valence: int | None = None
    importance: int | None = None
    score: float = 0.0
    outside_window: bool = False

    @property
    def who(self) -> str:
        return ", ".join(actor.name for actor in self.actors if actor.name)


@dataclass(frozen=True)
class Source:
    system: str
    installation: str
    container: str
    url: str = ""
    container_url: str = ""
    native_id: str = ""
    id_namespace: str = ""


@dataclass(frozen=True)
class OccurrenceActor:
    id: str
    name: str = ""
    image: str = ""
    url: str = ""
    type: Literal["person", "agent", "bot", "service", "group", "organisation"] = (
        "person"
    )
    role: str = ""


@dataclass(frozen=True)
class OccurrenceSubject:
    id: str
    type: str
    name: str = ""
    url: str = ""
    parent: str | None = None
    role: str = "about"
    attributes: dict[str, Any] = field(default_factory=dict)
    container_root: bool = False
    place_id: str | None = None


@dataclass(frozen=True)
class Occurrence:
    id: str
    type: str
    source: Source
    when: str | datetime | None = None
    ended_at: str | datetime | None = None
    what: str = ""
    title: str = ""
    why: str = ""
    actors: list[OccurrenceActor] = field(default_factory=list)
    subjects: list[OccurrenceSubject] = field(default_factory=list)
    part_of: str | None = None
    in_reply_to: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    valence: int | None = None
    importance: int | None = None
