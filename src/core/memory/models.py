from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from datetime import datetime
from src.core.scope import Scope


@dataclass(frozen=True)
class Source:
    system: str
    installation: str
    container: str
    url: str = ""
    container_url: str = ""
    native_id: str = ""
    id_namespace: str = ""

    @classmethod
    def for_scope(cls, scope: Scope) -> Source:
        return cls(
            system="sourceant",
            installation=scope.get("workspace", "local"),
            container=repr(
                tuple(
                    (key, value)
                    for key, value in scope.values
                    if key not in {"actor", "principal", "user", "agent"}
                )
            ),
        )


@dataclass(frozen=True)
class Actor:
    id: str
    name: str = ""
    type: Literal["person", "agent", "bot", "service", "group", "organisation"] = (
        "person"
    )
    role: str = ""
    url: str = ""
    image: str = ""


@dataclass(frozen=True)
class Subject:
    id: str
    type: str
    name: str = ""
    url: str = ""
    parent: str | None = None
    role: str = "about"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    source: Source
    when: datetime | None = None
    ended_at: datetime | None = None
    what: str = ""
    title: str = ""
    why: str = ""
    actors: list[Actor] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=list)
    part_of: str | None = None
    in_reply_to: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    valence: int | None = None
    importance: int | None = None
    score: float = 0.0
    outside_window: bool = False


@dataclass(frozen=True)
class EventPage:
    events: list[Event] = field(default_factory=list)
    next: str | None = None
