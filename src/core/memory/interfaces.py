from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from src.core.knowledge.models import KnowledgeObject

from src.core.scope import Scope
from .models import MemoryEvent


@dataclass(frozen=True)
class Remembered:
    what: str
    when: datetime
    why: str = ""
    who: str = ""
    paths: list[str] = field(default_factory=list)
    valence: int | None = None
    importance: int | None = None
    type: str = "sourceant.observation"


@dataclass(frozen=True)
class RememberedEvent:
    id: str
    when: datetime | None
    why: str
    who: str
    what: str
    type: str
    configured: bool = True
    score: float = 0.0


@dataclass(frozen=True)
class RecallResult:
    results: list[MemoryEvent] = field(default_factory=list)
    configured: bool = True


@dataclass(frozen=True)
class HistoryPage:
    events: list[MemoryEvent] = field(default_factory=list)
    next: str | None = None
    configured: bool = True


@dataclass(frozen=True)
class KnowledgeChange:
    type: Literal[
        "knowledge.captured",
        "knowledge.updated",
        "knowledge.status_changed",
        "knowledge.removed",
    ]
    actor: str
    before: KnowledgeObject | None = None
    after: KnowledgeObject | None = None


@dataclass(frozen=True)
class RepositoryDelivery:
    id: str
    type: str
    payload: dict
    stored: bool = True


@runtime_checkable
class Memory(Protocol):
    def remember(self, scope: Scope, observation: Remembered) -> RememberedEvent: ...
    def recall(self, scope: Scope, text: str, limit: int = 10) -> RecallResult: ...
    def history(
        self,
        scope: Scope,
        since: str | None = None,
        until: str | None = None,
        after: str | None = None,
    ) -> HistoryPage: ...
    def record_knowledge_change(
        self, scope: Scope, change: KnowledgeChange
    ) -> None: ...
    def record_repository_event(
        self, scope: Scope, delivery: RepositoryDelivery
    ) -> None: ...
