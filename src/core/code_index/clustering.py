"""Finding the parts of a graph that belong together.

Colour is what makes a drawing of a thousand symbols readable rather than a
cloud, and colour has to mean something. Grouping by folder only shows the
directory tree, which the reader already has. Grouping by how the code actually
connects shows which parts of it are one thing, which is the question.

The index runs Leiden of its own, but what it reports is a summary: how many
members a community has, its cohesion, and the names of its five biggest. It
never says which community a given symbol is in, so it cannot colour anything.
Hence this.

Modularity is what decides here: a part is a set of symbols with more connection
inside it than a graph of this shape would have by chance. Label propagation was
tried first and is kept below as a second implementation, but it merges two
clumps joined by a single call, which is exactly the boundary a reader cares
about.

Both are behind one protocol, because neither is the last word: Leiden, and
anything else taking nodes and edges and returning who belongs with whom, drops
in without the caller changing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.core.code_index import CodeEdge, CodeNode


@dataclass(frozen=True)
class Community:
    id: int
    name: str
    size: int


@dataclass(frozen=True)
class Clustering:
    """Which community each node is in, and what each community is called."""

    of: Mapping[str, int] = field(default_factory=dict)
    communities: tuple[Community, ...] = ()


@runtime_checkable
class GraphClustering(Protocol):
    def cluster(
        self, nodes: Sequence[CodeNode], edges: Sequence[CodeEdge]
    ) -> Clustering: ...


class Modularity:
    """Communities by whether moving a symbol makes the grouping tighter.

    Each symbol starts alone, and is then moved to whichever neighbouring
    community gains the most modularity, until nothing gains anything. Merging
    two clumps joined by one call loses modularity, so it does not happen: that
    single call is not enough connection to justify one group, which is what
    makes the boundary between subsystems come out where a reader expects it.

    Moving one symbol at a time only ever finds small groups: it answers "who are
    this function's closest friends", and a repository of nine hundred symbols has
    hundreds of those. So once nothing moves, each group is collapsed into a
    single node carrying its own connections, and the same question is asked
    again of those. Repeating that is what turns three hundred cliques into the
    dozen-or-so parts somebody would actually name.

    The visit order is fixed and a move only happens on a strict gain, so the same
    graph gives the same colours every time. That matters more than the last few
    percent of modularity when somebody is looking at it.
    """

    def __init__(self, rounds: int = 12, levels: int = 8):
        self._rounds = rounds
        self._levels = levels

    def cluster(
        self, nodes: Sequence[CodeNode], edges: Sequence[CodeEdge]
    ) -> Clustering:
        by_id = {node.id: node for node in nodes}
        order = sorted(by_id)
        index = {node_id: i for i, node_id in enumerate(order)}

        neighbours: dict[str, list[str]] = defaultdict(list)
        weights: dict[int, dict[int, float]] = defaultdict(dict)
        for edge in edges:
            if edge.source_id not in by_id or edge.target_id not in by_id:
                continue
            if edge.source_id == edge.target_id:
                continue
            # Undirected for this purpose: two functions are related whether one
            # calls the other or the other way round.
            neighbours[edge.source_id].append(edge.target_id)
            neighbours[edge.target_id].append(edge.source_id)
            a, b = index[edge.source_id], index[edge.target_id]
            weights[a][b] = weights[a].get(b, 0.0) + 1.0
            weights[b][a] = weights[b].get(a, 0.0) + 1.0

        of_index = self._levelled(len(order), weights)
        label = {node_id: str(of_index[index[node_id]]) for node_id in order}
        return self._named(by_id, label, neighbours)

    def _levelled(
        self, count: int, weights: Mapping[int, Mapping[int, float]]
    ) -> list[int]:
        """Group, collapse, and group again until collapsing changes nothing."""
        belongs = list(range(count))
        current: dict[int, dict[int, float]] = {
            i: dict(weights.get(i, {})) for i in range(count)
        }
        internal: dict[int, float] = dict.fromkeys(range(count), 0.0)

        for _ in range(self._levels):
            found = self._move(current, internal)
            if len(set(found.values())) == len(current):
                break
            belongs = [found[belongs[i]] for i in range(count)]
            current, internal = self._collapse(current, internal, found)
        return belongs

    def _move(
        self,
        adjacency: Mapping[int, Mapping[int, float]],
        internal: Mapping[int, float],
    ) -> dict[int, int]:
        """Move each node to the neighbouring group it fits best, until settled."""
        strength = {
            node: sum(joined.values()) + 2 * internal.get(node, 0.0)
            for node, joined in adjacency.items()
        }
        total = (sum(strength.values())) / 2
        belongs = {node: node for node in adjacency}
        if total <= 0:
            return belongs

        # How much strength each group holds, which is the term that stops a big
        # group from swallowing everything next to it.
        held = dict(strength)
        order = sorted(adjacency, key=lambda node: (-strength[node], node))

        for _ in range(self._rounds):
            settled = True
            for node in order:
                joined = adjacency[node]
                if not joined:
                    continue
                mine = belongs[node]
                held[mine] -= strength[node]

                shared: dict[int, float] = {}
                for other, weight in joined.items():
                    shared[belongs[other]] = shared.get(belongs[other], 0.0) + weight

                best = mine
                gain = shared.get(mine, 0.0) - held[mine] * strength[node] / (2 * total)
                for group in sorted(shared):
                    value = shared[group] - held[group] * strength[node] / (2 * total)
                    if value > gain:
                        best, gain = group, value

                held[best] += strength[node]
                if best != mine:
                    belongs[node] = best
                    settled = False
            if settled:
                break
        return belongs

    @staticmethod
    def _collapse(
        adjacency: Mapping[int, Mapping[int, float]],
        internal: Mapping[int, float],
        belongs: Mapping[int, int],
    ) -> tuple[dict[int, dict[int, float]], dict[int, float]]:
        """One node per group, carrying what it holds and what it reaches."""
        merged: dict[int, dict[int, float]] = {
            group: {} for group in set(belongs.values())
        }
        held: dict[int, float] = dict.fromkeys(merged, 0.0)
        for node, joined in adjacency.items():
            here = belongs[node]
            held[here] += internal.get(node, 0.0)
            for other, weight in joined.items():
                there = belongs[other]
                if there == here:
                    # Counted from both ends, so halve it back.
                    held[here] += weight / 2
                else:
                    merged[here][there] = merged[here].get(there, 0.0) + weight
        return merged, held

    @staticmethod
    def _named(
        by_id: Mapping[str, CodeNode],
        label: Mapping[str, str],
        neighbours: Mapping[str, Sequence[str]],
    ) -> Clustering:
        members: dict[str, list[str]] = defaultdict(list)
        for node_id, name in label.items():
            members[name].append(node_id)

        # Largest first, so the busiest part of the graph is community 0 and
        # keeps its colour as the graph grows.
        ranked = sorted(members.items(), key=lambda item: (-len(item[1]), item[0]))

        of: dict[str, int] = {}
        communities: list[Community] = []
        taken: set[str] = set()
        for index, (_, member_ids) in enumerate(ranked):
            for member_id in member_ids:
                of[member_id] = index
            communities.append(
                Community(
                    index,
                    _distinct(member_ids, by_id, neighbours, taken),
                    len(member_ids),
                )
            )
        return Clustering(of=of, communities=tuple(communities))


class LabelPropagation:
    """Communities by agreement between neighbours.

    Kept as a second implementation of the protocol rather than as the default.
    It is faster and simpler, but a node with no majority among its neighbours
    has to break the tie somehow, and any rule for that carries a label across a
    single call and merges two subsystems that a reader would not.
    """

    def __init__(self, rounds: int = 12):
        self._rounds = rounds

    def cluster(
        self, nodes: Sequence[CodeNode], edges: Sequence[CodeEdge]
    ) -> Clustering:
        by_id = {node.id: node for node in nodes}
        neighbours: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            if edge.source_id in by_id and edge.target_id in by_id:
                neighbours[edge.source_id].append(edge.target_id)
                neighbours[edge.target_id].append(edge.source_id)

        label: dict[str, str] = {node_id: node_id for node_id in by_id}
        order = sorted(by_id, key=lambda node_id: (-len(neighbours[node_id]), node_id))

        for _ in range(self._rounds):
            settled = True
            for node_id in order:
                adjacent = neighbours[node_id]
                if not adjacent:
                    continue
                counts = Counter(label[other] for other in adjacent)
                best = max(counts.values())
                chosen = min(name for name, n in counts.items() if n == best)
                if chosen != label[node_id]:
                    label[node_id] = chosen
                    settled = False
            if settled:
                break

        return Modularity._named(by_id, label, neighbours)


def _distinct(
    member_ids: Sequence[str],
    by_id: Mapping[str, CodeNode],
    neighbours: Mapping[str, Sequence[str]],
    taken: set[str],
) -> str:
    """A name no other community in this drawing is already using.

    Several communities can sit in one folder, and a legend listing `src/core`
    five times tells the reader nothing. The second one to want a name is named
    after what it is built around instead.
    """
    place = _name_for(member_ids, by_id, neighbours)
    if place not in taken:
        taken.add(place)
        return place

    around = _busiest_name(member_ids, by_id, neighbours)
    candidate = f"{place}/{around}" if place else around
    while candidate in taken:
        candidate += "'"
    taken.add(candidate)
    return candidate


def _name_for(
    member_ids: Sequence[str],
    by_id: Mapping[str, CodeNode],
    neighbours: Mapping[str, Sequence[str]],
) -> str:
    """What to call a community.

    Where its members live, when they mostly live in one place, because that is
    what a person would call it. Otherwise the symbol the rest of it points at,
    which is usually the thing the community exists around.
    """
    places = Counter(
        place
        for member_id in member_ids
        if (place := _top_folder(by_id[member_id].properties.get("file_path")))
    )
    if places:
        place, count = places.most_common(1)[0]
        if count * 2 >= len(member_ids):
            return place

    return _busiest_name(member_ids, by_id, neighbours)


def _busiest_name(
    member_ids: Sequence[str],
    by_id: Mapping[str, CodeNode],
    neighbours: Mapping[str, Sequence[str]],
) -> str:
    """The symbol the rest of the community is built around.

    A class outranks a function of the same busyness, because a community named
    `SuggestionFilter` tells the reader what it is and one named `get` does not.
    """
    busiest = max(
        member_ids,
        key=lambda member_id: (
            _tells_you_something(by_id[member_id]),
            len(neighbours[member_id]),
            member_id,
        ),
    )
    node = by_id[busiest]
    name = (node.properties or {}).get("name")
    return str(name) if name else busiest.rsplit(".", 1)[-1]


NAMEABLE = {"class": 2, "interface": 2, "struct": 2, "module": 1, "route": 1}


def _tells_you_something(node: CodeNode) -> int:
    return max(
        (NAMEABLE.get(label.lower(), 0) for label in (node.labels or ())), default=0
    )


def _top_folder(path: object) -> str:
    """The part of a path a person would name, skipping a bare `src`."""
    if not isinstance(path, str) or "/" not in path:
        return ""
    parts = [part for part in path.split("/")[:-1] if part]
    if not parts:
        return ""
    if parts[0] in {"src", "lib", "app"} and len(parts) > 1:
        return "/".join(parts[:2])
    return parts[0]


def degrees(nodes: Iterable[CodeNode], edges: Iterable[CodeEdge]) -> dict[str, int]:
    """How many lines meet at each node, which is what decides how it is drawn."""
    counted: dict[str, int] = {node.id: 0 for node in nodes}
    for edge in edges:
        if edge.source_id in counted:
            counted[edge.source_id] += 1
        if edge.target_id in counted:
            counted[edge.target_id] += 1
    return counted
