"""What a system holds, however deep it is nested.

A system holds parts, and some of those parts are systems holding parts of
their own. Answering "is this inside that" is a walk, and it was being done by
whoever asked: read the whole graph, follow the containment edges, decide. A
caller doing that has the whole graph in its hands to answer a question about
two identities, and it decides membership from data rather than asking the
thing that owns it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .interfaces import TopologyReader
from .models import TopologyTraversal
from ..scope import Scope

#: What a system is, and how it holds anything.
SYSTEM = "system"
CONTAINS = "contains"

#: How many seeds one walk of the graph accepts, and how much it answers with.
STEP = 50

#: How deep the nesting may go before this stops descending. A hierarchy has
#: no cycles and one parent per entity, so this is depth and not a guard
#: against looping.
MAX_DEPTH = 20


@dataclass(frozen=True)
class SystemContents:
    """Everything one system holds, transitively."""

    #: Systems nested inside it, at any depth.
    systems: tuple[str, ...] = ()
    #: Everything held that is not itself a system, by it or by those.
    assets: tuple[str, ...] = ()
    #: Whether the walk stopped before it ran out of graph. A caller deciding
    #: membership from a truncated answer would refuse things that are in
    #: fact inside, so it has to know.
    truncated: bool = False


def contents(
    reader: TopologyReader,
    scope: Scope,
    system_id: str,
    *,
    depth: int = MAX_DEPTH,
) -> SystemContents:
    """Walk the containment edges down from one system.

    One step at a time rather than in one traversal, because a traversal is
    bounded to three hops and a hierarchy is not.
    """
    systems: list[str] = []
    assets: list[str] = []
    seen = {system_id}
    frontier = [system_id]
    truncated = False

    for _ in range(max(1, depth)):
        if not frontier:
            break
        next_frontier: list[str] = []
        for start in range(0, len(frontier), STEP):
            batch = tuple(frontier[start : start + STEP])
            walked = reader.traverse(
                TopologyTraversal(
                    scope,
                    batch,
                    depth=1,
                    relationship_types=frozenset({CONTAINS}),
                    direction="outbound",
                    entity_limit=STEP,
                    relationship_limit=STEP * 10,
                )
            )
            truncated = truncated or walked.truncated
            kinds = {entity.id: entity.kind for entity in walked.entities}
            held = tuple(
                edge.target_id
                for edge in walked.relationships
                if edge.type == CONTAINS and edge.source_id in batch
            )
            for child in held:
                if child in seen:
                    continue
                seen.add(child)
                if kinds.get(child) == SYSTEM:
                    systems.append(child)
                    next_frontier.append(child)
                else:
                    assets.append(child)
        frontier = next_frontier
    else:
        truncated = truncated or bool(frontier)

    return SystemContents(
        systems=tuple(sorted(systems)),
        assets=tuple(sorted(assets)),
        truncated=truncated,
    )
