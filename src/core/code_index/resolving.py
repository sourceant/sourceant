from __future__ import annotations

from collections.abc import Callable

from .interfaces import CodeIndexReader
from .models import CodeSearch, CodeSearchResult, CodeTraversal, CodeTraversalResult


class ResolvingCodeIndexReader:
    def __init__(
        self,
        resolve: Callable[[], CodeIndexReader],
        fallback: CodeIndexReader,
    ) -> None:
        self._resolve = resolve
        self._fallback = fallback

    def search(self, query: CodeSearch) -> CodeSearchResult:
        return self._reader().search(query)

    def traverse(self, traversal: CodeTraversal) -> CodeTraversalResult:
        return self._reader().traverse(traversal)

    def _reader(self) -> CodeIndexReader:
        try:
            return self._resolve()
        except LookupError:
            return self._fallback
