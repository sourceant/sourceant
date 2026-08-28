from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    CodeEdge,
    CodeGraphQuery,
    CodeGraphResult,
    CodeNode,
    CodeSearch,
    CodeSearchResult,
    CodeTraversal,
    CodeTraversalResult,
)


@runtime_checkable
class CodeIndexReader(Protocol):
    def search(self, query: CodeSearch) -> CodeSearchResult: ...

    def traverse(self, traversal: CodeTraversal) -> CodeTraversalResult: ...


@runtime_checkable
class CodeGraphReader(Protocol):
    """Reading a whole scope at once, for drawing it.

    Separate from CodeIndexReader because an index is free not to be able to do
    this: search and traverse answer about a neighbourhood, and an index that can
    only answer those is still a usable index. A caller asks with isinstance and
    falls back to walking when the answer is no.
    """

    def graph(self, query: CodeGraphQuery) -> CodeGraphResult: ...


@runtime_checkable
class CodeIndexWriter(Protocol):
    def put_node(self, scope: Scope, node: CodeNode) -> None: ...

    def put_edge(self, scope: Scope, edge: CodeEdge) -> None: ...

    def clear(self, scope: Scope) -> None: ...


@runtime_checkable
class PathScopedCodeIndexWriter(Protocol):
    """Removing one file's nodes and edges without rebuilding the scope.

    Separate from CodeIndexWriter because an index is free not to be able to do
    this. A caller asks with isinstance and rebuilds the scope when the answer
    is no.
    """

    def remove_path(self, scope: Scope, file_path: str) -> None: ...


@runtime_checkable
class CodeIndexDigestReader(Protocol):
    """What each file hashed to when it was last indexed, keyed by path."""

    def file_digests(self, scope: Scope) -> dict[str, str]: ...


@runtime_checkable
class BulkCodeIndexWriter(Protocol):
    """Grouping many writes while a repository is indexed.

    What it yields answers checkpoint(), which the caller invokes wherever a
    flush would be safe. A repository too large to hold is committed in pieces
    at those points rather than all at the end.
    """

    def bulk_writes(self): ...


@runtime_checkable
class CodeIndexRepository(CodeIndexReader, CodeIndexWriter, Protocol):
    pass
