from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    TopologyEntity,
    TopologyRelationship,
    TopologySubgraph,
    TopologyTraversal,
)
from .snapshots import TopologySnapshot


@runtime_checkable
class TopologyReader(Protocol):
    def traverse(self, traversal: TopologyTraversal) -> TopologySubgraph: ...


@runtime_checkable
class TopologyWriter(Protocol):
    def put_entity(self, scope: Scope, entity: TopologyEntity) -> None: ...

    def put_relationship(
        self, scope: Scope, relationship: TopologyRelationship
    ) -> None: ...


@runtime_checkable
class TopologyRepository(TopologyReader, TopologyWriter, Protocol):
    pass


@runtime_checkable
class TopologySnapshotCodec(Protocol):
    media_type: str

    def encode(self, snapshot: TopologySnapshot) -> bytes: ...

    def decode(self, payload: bytes) -> TopologySnapshot: ...
