from __future__ import annotations

from collections import defaultdict, deque

from src.core.scope import Scope

from .models import (
    CodeEdge,
    CodeNode,
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
        matches = [
            node
            for (scope, _), node in self._nodes.items()
            if scope == query.scope
            and query.labels.issubset(node.labels)
            and all(
                node.properties.get(key) == value
                for key, value in query.properties.items()
            )
        ]
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
