from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Any, Mapping

from sqlalchemy import (
    Column,
    Engine,
    Index,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    or_,
    select,
)

from src.core.scope import Scope
from src.core.sql_support import chunked, rows_for

from .models import (
    CodeEdge,
    CodeGraphQuery,
    CodeGraphResult,
    CodeNode,
    CodeSearch,
    CodeSearchResult,
    CodeTraversal,
    CodeTraversalResult,
    is_excluded_path,
    is_test_path,
)

metadata = MetaData()
scope_key_type = String(500)

# How much of a repository the writer holds before committing what it has. A
# whole one does not fit: fifty thousand files come to some hundreds of
# thousands of nodes.
CHECKPOINT = 20_000

node_table = Table(
    "code_nodes",
    metadata,
    Column("scope", scope_key_type, primary_key=True),
    Column("id", String(500), primary_key=True),
    Column("file_path", String(500), nullable=True),
    Column("properties", Text, nullable=False),
    Index("ix_code_nodes_scope_file_path", "scope", "file_path"),
)

label_table = Table(
    "code_node_labels",
    metadata,
    Column("scope", scope_key_type, primary_key=True),
    Column("node_id", String(500), primary_key=True),
    Column("label", String(255), primary_key=True),
    Index("ix_code_node_labels_scope_label", "scope", "label"),
)

edge_table = Table(
    "code_edges",
    metadata,
    Column("scope", scope_key_type, primary_key=True),
    Column("id", String(500), primary_key=True),
    Column("source_id", String(500), nullable=False),
    Column("target_id", String(500), nullable=False),
    Column("type", String(255), nullable=False),
    Column("properties", Text, nullable=False),
    Index("ix_code_edges_scope_source", "scope", "source_id"),
    Index("ix_code_edges_scope_target", "scope", "target_id"),
)


class BulkWrite:
    def __init__(self, store: "SQLCodeIndexRepository") -> None:
        self._store = store

    def checkpoint(self) -> bool:
        return self._store.checkpoint()


class SQLCodeIndexRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        self._buffer: dict[str, list] | None = None
        self._checkpoint_every = CHECKPOINT
        if create_schema:
            metadata.create_all(engine)

    @contextmanager
    def bulk_writes(self, checkpoint_every: int | None = None) -> Iterator["BulkWrite"]:
        """Group writes, flushing whenever the caller says it is safe to.

        An edge is only written after the nodes it joins, so the store cannot
        decide on its own when a half written file is on the buffer. The caller
        marks the points where nothing is partial, and the store flushes at one
        of them once it is holding more than it should.
        """
        with self._lock:
            if self._buffer is not None:
                yield BulkWrite(self)
                return
            self._buffer = {"nodes": [], "edges": []}
            self._checkpoint_every = max(
                1, CHECKPOINT if checkpoint_every is None else checkpoint_every
            )
            try:
                yield BulkWrite(self)
                self._flush()
            finally:
                self._buffer = None

    def checkpoint(self) -> bool:
        """Flush if the buffer has grown past what should be held in memory."""
        with self._lock:
            if self._buffer is None:
                return False
            held = len(self._buffer["nodes"]) + len(self._buffer["edges"])
            if held < self._checkpoint_every:
                return False
            self._flush()
            self._buffer = {"nodes": [], "edges": []}
            return True

    def put_node(self, scope: Scope, node: CodeNode) -> None:
        with self._lock:
            if self._buffer is not None:
                self._buffer["nodes"].append((scope, node))
                return
            with self._engine.begin() as connection:
                self._write_nodes(connection, [(scope, node)])

    def put_edge(self, scope: Scope, edge: CodeEdge) -> None:
        with self._lock:
            if self._buffer is not None:
                self._buffer["edges"].append((scope, edge))
                return
            with self._engine.begin() as connection:
                self._require_endpoints(connection, [(scope, edge)])
                self._write_edges(connection, [(scope, edge)])

    def clear(self, scope: Scope) -> None:
        key = _scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                for table in (node_table, label_table, edge_table):
                    connection.execute(delete(table).where(table.c.scope == key))

    def remove_path(self, scope: Scope, file_path: str) -> None:
        key = _scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                ids = [
                    row[0]
                    for row in connection.execute(
                        select(node_table.c.id).where(
                            node_table.c.scope == key,
                            node_table.c.file_path == file_path,
                        )
                    )
                ]
                if not ids:
                    return
                for chunk in chunked(ids):
                    connection.execute(
                        delete(edge_table).where(
                            edge_table.c.scope == key,
                            or_(
                                edge_table.c.source_id.in_(chunk),
                                edge_table.c.target_id.in_(chunk),
                            ),
                        )
                    )
                    connection.execute(
                        delete(label_table).where(
                            label_table.c.scope == key,
                            label_table.c.node_id.in_(chunk),
                        )
                    )
                    connection.execute(
                        delete(node_table).where(
                            node_table.c.scope == key, node_table.c.id.in_(chunk)
                        )
                    )

    def file_digests(self, scope: Scope) -> dict[str, str]:
        key = _scope_key(scope)
        digests: dict[str, str] = {}
        with self._engine.connect() as connection:
            for row in connection.execute(
                select(node_table.c.file_path, node_table.c.properties)
                .join(
                    label_table,
                    (label_table.c.scope == node_table.c.scope)
                    & (label_table.c.node_id == node_table.c.id),
                )
                .where(node_table.c.scope == key, label_table.c.label == "File")
            ):
                path, properties = row[0], json.loads(row[1])
                digest = properties.get("digest")
                if isinstance(path, str) and isinstance(digest, str) and digest:
                    digests[path] = digest
        return digests

    def search(self, query: CodeSearch) -> CodeSearchResult:
        key = _scope_key(query.scope)
        statement = select(node_table).where(node_table.c.scope == key)
        if query.node_ids:
            statement = statement.where(node_table.c.id.in_(sorted(query.node_ids)))
        path = query.properties.get("file_path")
        if isinstance(path, str):
            statement = statement.where(node_table.c.file_path == path)
        statement = _with_labels(statement, key, query.labels, node_table.c.id)

        remaining = {
            name: value
            for name, value in query.properties.items()
            if name != "file_path" or not isinstance(value, str)
        }
        with self._engine.connect() as connection:
            if not remaining:
                return self._paged_in_sql(connection, key, statement, query)
            return self._paged_in_python(connection, key, statement, query, remaining)

    def _paged_in_sql(self, connection, key, statement, query) -> CodeSearchResult:
        total = connection.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        rows = list(
            connection.execute(
                statement.order_by(node_table.c.id)
                .limit(query.limit)
                .offset(query.offset)
            ).mappings()
        )
        labels = self._labels_for(connection, key, [row["id"] for row in rows])
        nodes = tuple(
            _node_from_row(row, labels.get(row["id"], frozenset())) for row in rows
        )
        return CodeSearchResult(
            nodes=nodes,
            total=total,
            has_more=query.offset + len(nodes) < total,
        )

    def _paged_in_python(
        self, connection, key, statement, query, remaining
    ) -> CodeSearchResult:
        rows = [
            row
            for row in connection.execute(
                statement.order_by(node_table.c.id)
            ).mappings()
            if all(
                json.loads(row["properties"]).get(name) == value
                for name, value in remaining.items()
            )
        ]
        page = rows[query.offset : query.offset + query.limit]
        labels = self._labels_for(connection, key, [row["id"] for row in page])
        nodes = tuple(
            _node_from_row(row, labels.get(row["id"], frozenset())) for row in page
        )
        return CodeSearchResult(
            nodes=nodes,
            total=len(rows),
            has_more=query.offset + len(nodes) < len(rows),
        )

    def traverse(self, traversal: CodeTraversal) -> CodeTraversalResult:
        key = _scope_key(traversal.scope)
        nodes: list[CodeNode] = []
        edges: dict[str, CodeEdge] = {}
        visited: set[str] = set()
        truncated = False

        rows: list = []
        with self._engine.connect() as connection:
            frontier = list(traversal.node_ids)
            distance = 0
            while frontier:
                fetched = []
                found = self._node_rows(connection, key, frontier)
                for node_id in frontier:
                    if node_id in visited:
                        continue
                    row = found.get(node_id)
                    if row is None:
                        continue
                    if len(rows) >= traversal.node_limit:
                        truncated = True
                        continue
                    visited.add(node_id)
                    rows.append(row)
                    fetched.append(node_id)
                if distance == traversal.depth or not fetched:
                    break
                next_frontier: list[str] = []
                for edge in self._edges_touching(connection, key, fetched, traversal):
                    edges[edge.id] = edge
                    for candidate in (edge.source_id, edge.target_id):
                        if candidate not in visited:
                            next_frontier.append(candidate)
                frontier = list(dict.fromkeys(next_frontier))
                distance += 1

            labels = self._labels_for(connection, key, [row["id"] for row in rows])
            nodes = [
                _node_from_row(row, labels.get(row["id"], frozenset())) for row in rows
            ]

        included = {node.id for node in nodes}
        packed = tuple(
            edge
            for edge in edges.values()
            if edge.source_id in included and edge.target_id in included
        )
        return CodeTraversalResult(
            nodes=tuple(nodes),
            edges=packed,
            truncated=truncated or len(packed) != len(edges),
        )

    def graph(self, query: CodeGraphQuery) -> CodeGraphResult:
        key = _scope_key(query.scope)
        statement = select(node_table).where(node_table.c.scope == key)
        if query.path_prefix:
            statement = statement.where(
                node_table.c.file_path.like(
                    f"{_escape_like(query.path_prefix)}%", escape="\\"
                )
            )
        statement = _with_any_label(statement, key, query.labels, node_table.c.id)
        statement = statement.order_by(node_table.c.id)

        rows: list = []
        truncated = False
        with self._engine.connect() as connection:
            result = connection.execution_options(stream_results=True).execute(
                statement
            )
            for row in result.mappings():
                if not _drawable_row(json.loads(row["properties"]), query):
                    continue
                if len(rows) >= query.node_limit:
                    truncated = True
                    break
                rows.append(row)
            result.close()

            labels = self._labels_for(connection, key, [row["id"] for row in rows])
            kept = tuple(
                _node_from_row(row, labels.get(row["id"], frozenset())) for row in rows
            )
            included = {node.id for node in kept}
            edges = self._edges_between(connection, key, included, query.edge_types)
        return CodeGraphResult(nodes=kept, edges=edges, truncated=truncated)

    def _flush(self) -> None:
        buffered = self._buffer or {"nodes": [], "edges": []}
        if not buffered["nodes"] and not buffered["edges"]:
            return
        with self._engine.begin() as connection:
            if buffered["nodes"]:
                self._write_nodes(connection, buffered["nodes"])
            if buffered["edges"]:
                self._require_endpoints(connection, buffered["edges"])
                self._write_edges(connection, buffered["edges"])

    def _write_nodes(self, connection, entries) -> None:
        by_key: dict[tuple[str, str], tuple[Scope, CodeNode]] = {}
        for scope, node in entries:
            by_key[(_scope_key(scope), node.id)] = (scope, node)
        by_scope: dict[str, list[str]] = {}
        for scope_key, node_id in by_key:
            by_scope.setdefault(scope_key, []).append(node_id)
        for scope_key, node_ids in by_scope.items():
            for chunk in chunked(node_ids):
                connection.execute(
                    delete(node_table).where(
                        node_table.c.scope == scope_key,
                        node_table.c.id.in_(chunk),
                    )
                )
                connection.execute(
                    delete(label_table).where(
                        label_table.c.scope == scope_key,
                        label_table.c.node_id.in_(chunk),
                    )
                )
        node_rows = []
        label_rows = []
        for (scope_key, node_id), (_, node) in by_key.items():
            path = node.properties.get("file_path")
            node_rows.append(
                {
                    "scope": scope_key,
                    "id": node_id,
                    "file_path": path if isinstance(path, str) else None,
                    "properties": _encode(node.properties),
                }
            )
            for label in sorted(node.labels):
                label_rows.append(
                    {"scope": scope_key, "node_id": node_id, "label": label}
                )
        connection.execute(node_table.insert(), node_rows)
        if label_rows:
            connection.execute(label_table.insert(), label_rows)

    def _write_edges(self, connection, entries) -> None:
        by_key: dict[tuple[str, str], tuple[Scope, CodeEdge]] = {}
        for scope, edge in entries:
            by_key[(_scope_key(scope), edge.id)] = (scope, edge)
        by_scope: dict[str, list[str]] = {}
        for scope_key, edge_id in by_key:
            by_scope.setdefault(scope_key, []).append(edge_id)
        for scope_key, edge_ids in by_scope.items():
            for chunk in chunked(edge_ids):
                connection.execute(
                    delete(edge_table).where(
                        edge_table.c.scope == scope_key,
                        edge_table.c.id.in_(chunk),
                    )
                )
        connection.execute(
            edge_table.insert(),
            [
                {
                    "scope": scope_key,
                    "id": edge_id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "type": edge.type,
                    "properties": _encode(edge.properties),
                }
                for (scope_key, edge_id), (_, edge) in by_key.items()
            ],
        )

    def _require_endpoints(self, connection, entries) -> None:
        wanted: dict[str, set[str]] = {}
        for scope, edge in entries:
            wanted.setdefault(_scope_key(scope), set()).update(
                (edge.source_id, edge.target_id)
            )
        pending = {scope_key: set(ids) for scope_key, ids in wanted.items() if ids}
        if self._buffer is not None:
            for scope, node in self._buffer["nodes"]:
                pending.get(_scope_key(scope), set()).discard(node.id)
        for scope_key, ids in pending.items():
            if not ids:
                continue
            found = {
                row[0]
                for row in connection.execute(
                    select(node_table.c.id).where(
                        node_table.c.scope == scope_key,
                        node_table.c.id.in_(sorted(ids)),
                    )
                )
            }
            missing = ids - found
            if missing:
                raise ValueError(
                    "edge endpoints must exist in the same scope: "
                    + ", ".join(sorted(missing))
                )

    def _labels_for(
        self, connection, scope_key: str, node_ids
    ) -> dict[str, frozenset[str]]:
        grouped: dict[str, set[str]] = {}
        for row in rows_for(
            node_ids,
            lambda chunk: connection.execute(
                select(label_table.c.node_id, label_table.c.label).where(
                    label_table.c.scope == scope_key,
                    label_table.c.node_id.in_(chunk),
                )
            ),
        ):
            grouped.setdefault(row[0], set()).add(row[1])
        return {node_id: frozenset(values) for node_id, values in grouped.items()}

    def _node_rows(self, connection, scope_key, node_ids) -> dict:
        return {
            row["id"]: row
            for row in rows_for(
                node_ids,
                lambda chunk: connection.execute(
                    select(node_table).where(
                        node_table.c.scope == scope_key,
                        node_table.c.id.in_(chunk),
                    )
                ).mappings(),
            )
        }

    def _edges_touching(
        self, connection, scope_key, node_ids, traversal: CodeTraversal
    ) -> list[CodeEdge]:
        if not node_ids:
            return []
        statement = select(edge_table).where(edge_table.c.scope == scope_key)
        if traversal.direction == "outbound":
            statement = statement.where(edge_table.c.source_id.in_(node_ids))
        elif traversal.direction == "inbound":
            statement = statement.where(edge_table.c.target_id.in_(node_ids))
        else:
            statement = statement.where(
                or_(
                    edge_table.c.source_id.in_(node_ids),
                    edge_table.c.target_id.in_(node_ids),
                )
            )
        if traversal.edge_types:
            statement = statement.where(
                edge_table.c.type.in_(sorted(traversal.edge_types))
            )
        statement = statement.order_by(edge_table.c.id)
        return [_edge_from_row(row) for row in connection.execute(statement).mappings()]

    def _edges_between(
        self, connection, scope_key, node_ids: set[str], edge_types
    ) -> tuple[CodeEdge, ...]:
        if not node_ids:
            return ()

        def _for(chunk):
            statement = select(edge_table).where(
                edge_table.c.scope == scope_key,
                edge_table.c.source_id.in_(chunk),
            )
            if edge_types:
                statement = statement.where(edge_table.c.type.in_(sorted(edge_types)))
            return connection.execute(statement).mappings()

        wanted = set(node_ids)
        found = {
            row["id"]: _edge_from_row(row)
            for row in rows_for(node_ids, _for)
            if row["target_id"] in wanted
        }
        return tuple(found[key] for key in sorted(found))


def _with_labels(statement, scope_key: str, labels, id_column):
    for label in sorted(labels):
        statement = statement.where(
            select(label_table.c.label)
            .where(
                label_table.c.scope == scope_key,
                label_table.c.node_id == id_column,
                label_table.c.label == label,
            )
            .exists()
        )
    return statement


def _with_any_label(statement, scope_key: str, labels, id_column):
    if not labels:
        return statement
    return statement.where(
        select(label_table.c.label)
        .where(
            label_table.c.scope == scope_key,
            label_table.c.node_id == id_column,
            label_table.c.label.in_(sorted(labels)),
        )
        .exists()
    )


def _drawable_row(properties: Mapping[str, Any], query: CodeGraphQuery) -> bool:
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


def _node_from_row(row: Mapping[str, Any], labels: frozenset[str]) -> CodeNode:
    return CodeNode(
        id=row["id"],
        labels=labels,
        properties=json.loads(row["properties"]),
    )


def _edge_from_row(row: Mapping[str, Any]) -> CodeEdge:
    return CodeEdge(
        id=row["id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        type=row["type"],
        properties=json.loads(row["properties"]),
    )


def _scope_key(scope: Scope) -> str:
    return json.dumps(scope.values, separators=(",", ":"))


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
