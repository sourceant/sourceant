from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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
