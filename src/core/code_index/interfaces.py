from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    CodeEdge,
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
class CodeIndexWriter(Protocol):
    def put_node(self, scope: Scope, node: CodeNode) -> None: ...

    def put_edge(self, scope: Scope, edge: CodeEdge) -> None: ...

    def clear(self, scope: Scope) -> None: ...


@runtime_checkable
class CodeIndexRepository(CodeIndexReader, CodeIndexWriter, Protocol):
    pass
