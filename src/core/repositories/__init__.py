"""The repositories a workspace covers: rows when hosted, folders when personal."""

from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from src.core.services import ServiceRegistry, service_registry


@runtime_checkable
class RepositoryRegistry(Protocol):
    def all(self, workspace: str) -> Sequence[Any]: ...

    def named(self, workspace: str, name: str) -> Any: ...

    def add(self, workspace: str, path: str, *, name: str = "") -> Any: ...

    def remove(self, workspace: str, path: str) -> bool: ...


def registry(
    services: ServiceRegistry = service_registry,
) -> Optional[RepositoryRegistry]:
    try:
        return services.resolve(RepositoryRegistry)
    except LookupError:
        return None


__all__ = ["RepositoryRegistry", "registry"]
