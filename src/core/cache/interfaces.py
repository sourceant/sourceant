"""A place to keep something that can be worked out again."""

from __future__ import annotations

from typing import NamedTuple, Optional, Protocol, runtime_checkable


class Owner(NamedTuple):
    """Who a cached entry belongs to.

    Not src.core.scope.Scope, which is what a request is answered within.
    """

    type: str
    id: str


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

    def set(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        ttl: int,
        scope: Optional[Owner] = None,
    ) -> None: ...

    def forget(self, namespace: str, key: str) -> None: ...

    def clear(self, namespace: str, scope: Optional[Owner] = None) -> int:
        """Drop a namespace, or only what belongs to one scope, and say how many.

        A scope nobody recorded cannot be cleared by scope: the key is a hash
        of whatever the caller put in it, so nothing on an entry says who it
        was for unless the writer said so.
        """
        ...
