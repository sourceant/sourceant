from __future__ import annotations

from collections import defaultdict, deque

from src.core.scope import Scope

from .models import (
    CodeEdge,
    CodeGraphQuery,
    CodeGraphResult,
    CodeNode,
    is_excluded_path,
    is_test_path,
    CodeSearch,
    CodeSearchResult,
    CodeTraversal,
    CodeTraversalResult,
)


class InMemoryCodeIndex:
    def __init__(self) -> None:
        self._nodes: dict[tuple[Scope, str], CodeNode] = {}
        self._edges: dict[tuple[Scope, str], CodeEdge] = {}
        self._adjacency: dict[tuple[Scope, str], set[str]] = defaultdict(set)

    def put_node(self, scope: Scope, node: CodeNode) -> None:
        self._nodes[(scope, node.id)] = node

    def put_edge(self, scope: Scope, edge: CodeEdge) -> None:
        source = scope, edge.source_id
        target = scope, edge.target_id
        if source not in self._nodes or target not in self._nodes:
            raise ValueError("edge endpoints must exist in the same scope")
        edge_key = scope, edge.id
        previous = self._edges.get(edge_key)
        if previous:
            self._adjacency[(scope, previous.source_id)].discard(previous.id)
            self._adjacency[(scope, previous.target_id)].discard(previous.id)
        self._edges[edge_key] = edge
        self._adjacency[source].add(edge.id)
        self._adjacency[target].add(edge.id)

    def clear(self, scope: Scope) -> None:
        for key in list(self._nodes):
            if key[0] == scope:
                del self._nodes[key]
                self._adjacency.pop(key, None)
        for key in list(self._edges):
            if key[0] == scope:
                del self._edges[key]

    def search(self, query: CodeSearch) -> CodeSearchResult:
        if query.node_ids:
            candidates = (
                self._nodes.get((query.scope, node_id)) for node_id in query.node_ids
            )
        else:
            candidates = (
                node for (scope, _), node in self._nodes.items() if scope == query.scope
            )
        matches = [
            node
            for node in candidates
            if node is not None
            and query.labels.issubset(node.labels)
            and all(
                node.properties.get(key) == value
                for key, value in query.properties.items()
            )
        ]
        matches.sort(key=lambda node: node.id)
        nodes = tuple(matches[query.offset : query.offset + query.limit])
        return CodeSearchResult(
            nodes=nodes,
            total=len(matches),
            has_more=query.offset + len(nodes) < len(matches),
        )

    def traverse(self, traversal: CodeTraversal) -> CodeTraversalResult:
        scope = traversal.scope
        queue = deque(
            (node, 0)
            for node_id in traversal.node_ids
            if (node := self._nodes.get((scope, node_id)))
        )
        visited: set[str] = set()
        nodes: list[CodeNode] = []
        edges: dict[str, CodeEdge] = {}
        truncated = False

        while queue:
            node, distance = queue.popleft()
            if node.id in visited:
                continue
            if len(nodes) >= traversal.node_limit:
                truncated = True
                continue
            visited.add(node.id)
            nodes.append(node)
            if distance == traversal.depth:
                continue

            for edge_id in sorted(self._adjacency[(scope, node.id)]):
                edge = self._edges[(scope, edge_id)]
                if traversal.edge_types and edge.type not in traversal.edge_types:
                    continue
                if traversal.direction == "outbound" and edge.source_id != node.id:
                    continue
                if traversal.direction == "inbound" and edge.target_id != node.id:
                    continue
                other_id = (
                    edge.target_id if edge.source_id == node.id else edge.source_id
                )
                target = self._nodes.get((scope, other_id))
                if target:
                    queue.append((target, distance + 1))
                edges[edge.id] = edge

        included = {node.id for node in nodes}
        packed_edges = tuple(
            edge
            for edge in edges.values()
            if edge.source_id in included and edge.target_id in included
        )
        return CodeTraversalResult(
            nodes=tuple(nodes),
            edges=packed_edges,
            truncated=truncated or len(packed_edges) != len(edges),
        )

    def graph(self, query: CodeGraphQuery) -> CodeGraphResult:
        """Everything in the scope, for drawing rather than for reading."""
        scope = query.scope
        wanted = [
            node
            for (node_scope, _), node in self._nodes.items()
            if node_scope == scope and _drawable(node, query)
        ]
        wanted.sort(key=lambda node: node.id)
        truncated = len(wanted) > query.node_limit
        kept = wanted[: query.node_limit]
        included = {node.id for node in kept}

        # Narrowed before it is ordered. Sorting every edge in the store to
        # answer about one scope costs the whole store on every call.
        wanted_edges = [
            (edge_id, edge)
            for (edge_scope, edge_id), edge in self._edges.items()
            if edge_scope == scope
            and edge.source_id in included
            and edge.target_id in included
            and (not query.edge_types or edge.type in query.edge_types)
        ]
        wanted_edges.sort(key=lambda pair: pair[0])
        edges = tuple(edge for _, edge in wanted_edges)
        return CodeGraphResult(nodes=tuple(kept), edges=edges, truncated=truncated)


def _drawable(node: CodeNode, query: CodeGraphQuery) -> bool:
    if query.labels and not (node.labels & query.labels):
        return False
    properties = node.properties or {}
    path = properties.get("file_path")
    if query.path_prefix and not (
        isinstance(path, str) and path.startswith(query.path_prefix)
    ):
        return False
    if not query.include_tests and (properties.get("is_test") or is_test_path(path)):
        return False
    if query.excluded_paths and is_excluded_path(path, query.excluded_paths):
        return False
    return True
