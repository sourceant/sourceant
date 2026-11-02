from typing import Protocol, runtime_checkable

from src.core.scope import Scope
from .models import Entity, Event


@runtime_checkable
class EventRecorder(Protocol):
    def record(self, events: list[Event], entities: list[Entity]) -> None: ...


@runtime_checkable
class EventReader(Protocol):
    def list(
        self,
        scope: Scope,
        since: str | None = None,
        until: str | None = None,
        after: str | None = None,
    ) -> dict: ...

    def recall(self, query: dict) -> dict: ...


@runtime_checkable
class BucketBinder(Protocol):
    repository_sources: dict[str, list[dict]]

    def bucket(self, scope: Scope) -> str: ...

    def buckets(self, scope: Scope) -> list[str]: ...

    def bind(self, system: str, installation: str, container: str) -> str: ...
