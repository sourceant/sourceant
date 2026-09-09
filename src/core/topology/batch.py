import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from src.core.scope import Scope
from .models import TopologyEntity, TopologyRelationship


@dataclass(frozen=True)
class TopologyBatch:
    operation_id: str
    entities: tuple[TopologyEntity, ...] = ()
    relationships: tuple[TopologyRelationship, ...] = ()
    remove_relationships: tuple[str, ...] = ()

    @property
    def digest(self):
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@runtime_checkable
class TopologyBatchWriter(Protocol):
    def apply_batch(self, scope: Scope, batch: TopologyBatch) -> None: ...


def validate_hierarchy(entities, relationships):
    parents = {}
    for edge in relationships:
        if edge.type != "contains":
            continue
        previous = parents.setdefault(edge.target_id, edge.source_id)
        if previous != edge.source_id:
            raise ValueError("An entity may have only one parent")
    for child in parents:
        seen = set()
        current = child
        while current in parents:
            if current in seen:
                raise ValueError("System hierarchy cannot contain a cycle")
            seen.add(current)
            current = parents[current]
