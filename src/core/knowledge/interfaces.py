from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    KnowledgeLink,
    KnowledgeSelection,
    KnowledgeObject,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeResult,
    KnowledgeSubgraph,
    KnowledgeTraversal,
)


@runtime_checkable
class KnowledgeReader(Protocol):
    def search(self, query: KnowledgeQuery) -> KnowledgeResult: ...

    def get_relationships(
        self,
        scope: Scope,
        knowledge_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[KnowledgeRelationship, ...]: ...

    def traverse(self, traversal: KnowledgeTraversal) -> KnowledgeSubgraph: ...


@runtime_checkable
class KnowledgeWriter(Protocol):
    def put(self, scope: Scope, knowledge: KnowledgeObject) -> None: ...

    def put_relationship(
        self, scope: Scope, relationship: KnowledgeRelationship
    ) -> None: ...


@runtime_checkable
class KnowledgeRepository(KnowledgeReader, KnowledgeWriter, Protocol):
    pass


@runtime_checkable
class KnowledgeLinkReader(Protocol):
    """What a knowledge object is attached to in the code.

    Separate from KnowledgeReader because a store is free not to hold links.
    A caller asks with isinstance and does without when the answer is no.
    """

    def get_links(
        self, scope: Scope, knowledge_ids: frozenset[str]
    ) -> tuple[KnowledgeLink, ...]: ...

    def knowledge_ids_for_paths(
        self, scope: Scope, paths: frozenset[str]
    ) -> frozenset[str]: ...


@runtime_checkable
class KnowledgeLinkWriter(Protocol):
    def put_link(self, scope: Scope, link: KnowledgeLink) -> None: ...


@runtime_checkable
class KnowledgeSelector(Protocol):
    """Which recorded knowledge a change should be judged against.

    The core answers this from the links it holds, which is exact and shallow.
    Reading intent out of a change is a different problem, and an installation
    that can do it registers its own selector here.
    """

    def select(self, selection: KnowledgeSelection) -> tuple[KnowledgeObject, ...]: ...
