"""A place to keep something that can be worked out again."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """Keyed storage with an expiry, holding text.

    Text rather than objects: a caller that has something else serialises it,
    and every backend can hold a string without agreeing on anything further.

    Every method is best effort. Whatever is cached can be worked out again by
    definition, so an implementation reports a failure and behaves as though it
    were empty rather than raising into a caller that has work to finish.
    """

    def get(self, namespace: str, key: str) -> Optional[str]: ...

    def set(self, namespace: str, key: str, value: str, *, ttl: int) -> None: ...

    def forget(self, namespace: str, key: str) -> None: ...
