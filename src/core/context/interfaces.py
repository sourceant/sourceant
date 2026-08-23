from typing import Protocol, runtime_checkable

from .models import ContextPack, ContextRequest


@runtime_checkable
class ContextProvider(Protocol):
    def get_context(self, request: ContextRequest) -> ContextPack: ...
