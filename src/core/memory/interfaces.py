from typing import Protocol, runtime_checkable

from src.core.scope import Scope
from .models import Event, EventPage


@runtime_checkable
class Memory(Protocol):
    configured: bool

    def record(self, scope: Scope, events: list[Event]) -> list[Event]: ...
    def events(self, scope: Scope, since=None, until=None, after=None) -> EventPage: ...
    def recall(self, scope: Scope, text: str, limit: int = 10) -> list[Event]: ...
