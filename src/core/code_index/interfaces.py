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
class CodeIndexRepository(CodeIndexReader, CodeIndexWriter, Protocol):
    pass
