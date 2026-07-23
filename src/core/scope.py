from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class Scope:
    values: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.values]
        if len(keys) != len(set(keys)):
            raise ValueError("scope keys must be unique")
        if any(not key or not value for key, value in self.values):
            raise ValueError("scope keys and values must not be empty")
        object.__setattr__(self, "values", tuple(sorted(self.values)))

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Scope":
        return cls(tuple(sorted(values.items())))

    def get(self, key: str, default: str | None = None) -> str | None:
        return dict(self.values).get(key, default)

    def extend(self, values: Mapping[str, str]) -> "Scope":
        combined = dict(self.values)
        combined.update(values)
        return self.from_mapping(combined)


@dataclass(frozen=True)
class ScopeReference:
    kind: str
    id: str
    qualifiers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.kind or not self.id:
            raise ValueError("scope reference kind and identity must not be empty")
        keys = [key for key, _ in self.qualifiers]
        if len(keys) != len(set(keys)):
            raise ValueError("scope reference qualifier keys must be unique")
        if any(not key or not value for key, value in self.qualifiers):
            raise ValueError("scope reference qualifiers must not be empty")
        object.__setattr__(self, "qualifiers", tuple(sorted(self.qualifiers)))

    @classmethod
    def from_mapping(
        cls,
        kind: str,
        id: str,
        qualifiers: Mapping[str, str],
    ) -> "ScopeReference":
        return cls(kind, id, tuple(sorted(qualifiers.items())))

    def get(self, key: str, default: str | None = None) -> str | None:
        return dict(self.qualifiers).get(key, default)


@runtime_checkable
class ScopeResolver(Protocol):
    def resolve(self, reference: ScopeReference) -> Scope | None: ...


@runtime_checkable
class ScopeBindingWriter(Protocol):
    def bind(self, reference: ScopeReference, scope: Scope) -> None: ...

    def unbind(self, reference: ScopeReference) -> None: ...


@runtime_checkable
class ScopeRepository(ScopeResolver, ScopeBindingWriter, Protocol):
    pass


class InMemoryScopeRepository:
    def __init__(
        self,
        bindings: Mapping[ScopeReference, Scope] | None = None,
    ) -> None:
        self._bindings = dict(bindings or {})

    def resolve(self, reference: ScopeReference) -> Scope | None:
        return self._bindings.get(reference)

    def bind(self, reference: ScopeReference, scope: Scope) -> None:
        self._bindings[reference] = scope

    def unbind(self, reference: ScopeReference) -> None:
        self._bindings.pop(reference, None)
