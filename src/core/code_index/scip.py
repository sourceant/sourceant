from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.core.scope import Scope

from .interfaces import CodeIndexWriter
from .models import CodeEdge, CodeNode


@dataclass(frozen=True)
class ScipImportLimits:
    payload_byte_limit: int = 500_000_000
    document_limit: int = 10_000
    symbol_limit: int = 1_000_000
    occurrence_limit: int = 5_000_000
    relationship_limit: int = 5_000_000

    def __post_init__(self) -> None:
        if (
            min(
                self.payload_byte_limit,
                self.document_limit,
                self.symbol_limit,
                self.occurrence_limit,
                self.relationship_limit,
            )
            < 1
        ):
            raise ValueError("SCIP import limits must be positive")


@dataclass(frozen=True)
class ScipImportResult:
    documents: int
    symbols: int
    occurrences: int
    relationships: int


class ScipJsonImporter:
    DEFINITION_ROLE = 0x1
    IMPORT_ROLE = 0x2

    def __init__(
        self,
        writer: CodeIndexWriter,
        *,
        limits: ScipImportLimits | None = None,
    ) -> None:
        self._writer = writer
        self._limits = limits or ScipImportLimits()

    def import_json(self, scope: Scope, payload: str | bytes) -> ScipImportResult:
        size = (
            len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        )
        if size > self._limits.payload_byte_limit:
            raise ValueError("SCIP payload limit exceeded")
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("SCIP payload must be valid JSON") from exc
        return self.import_index(scope, _mapping(decoded, "index"))

    def import_index(
        self,
        scope: Scope,
        payload: Mapping[str, Any],
    ) -> ScipImportResult:
        revision = scope.get("revision")
        if not revision:
            raise ValueError("SCIP imports require a revision in the target scope")
        metadata = _mapping(payload.get("metadata"), "metadata")
        documents = _sequence(payload.get("documents", ()), "documents")
        external_symbols = _sequence(
            payload.get("external_symbols", ()), "external_symbols"
        )
        if len(documents) > self._limits.document_limit:
            raise ValueError("SCIP document limit exceeded")

        tool_info = _optional_mapping(metadata.get("tool_info"), "metadata.tool_info")
        common = {
            "revision": revision,
            "source": "scip",
            "project_root": _optional_string(metadata.get("project_root")),
            "indexer": _optional_string(tool_info.get("name")),
            "indexer_version": _optional_string(tool_info.get("version")),
        }
        nodes: dict[str, CodeNode] = {}
        edges: dict[str, CodeEdge] = {}
        symbol_count = 0
        occurrence_count = 0
        relationship_count = 0
        seen_paths: set[str] = set()

        for raw_symbol in external_symbols:
            symbol_count, relationship_count = self._collect_symbol(
                _mapping(raw_symbol, "external_symbols[]"),
                nodes,
                edges,
                common,
                symbol_count,
                relationship_count,
            )

        for document_index, raw_document in enumerate(documents):
            document = _mapping(raw_document, "documents[]")
            path = _relative_path(document.get("relative_path"))
            if path in seen_paths:
                raise ValueError("SCIP document paths must be unique")
            seen_paths.add(path)
            language = _optional_string(document.get("language"))
            file_id = _identity("file", path)
            nodes[file_id] = CodeNode(
                id=file_id,
                labels=frozenset({"File"}),
                properties={**common, "file_path": path, "language": language},
            )

            for raw_symbol in _sequence(document.get("symbols", ()), "symbols"):
                symbol_count, relationship_count = self._collect_symbol(
                    _mapping(raw_symbol, "symbols[]"),
                    nodes,
                    edges,
                    {**common, "file_path": path, "language": language},
                    symbol_count,
                    relationship_count,
                )

            occurrences = _sequence(document.get("occurrences", ()), "occurrences")
            occurrence_count += len(occurrences)
            if occurrence_count > self._limits.occurrence_limit:
                raise ValueError("SCIP occurrence limit exceeded")
            for occurrence_index, raw_occurrence in enumerate(occurrences):
                occurrence = _mapping(raw_occurrence, "occurrences[]")
                symbol = _optional_string(occurrence.get("symbol"))
                if not symbol:
                    continue
                symbol_id = _identity("symbol", symbol)
                existing = nodes.get(symbol_id)
                if existing is None:
                    symbol_count += 1
                    self._check_symbol_limit(symbol_count)
                    nodes[symbol_id] = CodeNode(
                        id=symbol_id,
                        labels=frozenset({"Symbol"}),
                        properties={
                            **common,
                            "symbol": symbol,
                            "referenced_from": path,
                        },
                    )
                roles = _integer(occurrence.get("symbol_roles", 0), "symbol_roles")
                for edge_type in self._occurrence_edge_types(roles):
                    edge_id = _identity(
                        "occurrence",
                        ":".join(
                            (
                                str(document_index),
                                str(occurrence_index),
                                path,
                                symbol,
                                edge_type,
                            )
                        ),
                    )
                    edges[edge_id] = CodeEdge(
                        id=edge_id,
                        source_id=file_id,
                        target_id=symbol_id,
                        type=edge_type,
                        properties={
                            **common,
                            "file_path": path,
                            "range": _range_value(occurrence),
                            "symbol_roles": roles,
                        },
                    )

        self._writer.clear(scope)
        for node in sorted(nodes.values(), key=lambda item: item.id):
            self._writer.put_node(scope, node)
        for edge in sorted(edges.values(), key=lambda item: item.id):
            self._writer.put_edge(scope, edge)
        return ScipImportResult(
            documents=len(documents),
            symbols=symbol_count,
            occurrences=occurrence_count,
            relationships=relationship_count,
        )

    def _collect_symbol(
        self,
        symbol_info: Mapping[str, Any],
        nodes: dict[str, CodeNode],
        edges: dict[str, CodeEdge],
        common: Mapping[str, Any],
        symbol_count: int,
        relationship_count: int,
    ) -> tuple[int, int]:
        symbol = _required_string(symbol_info.get("symbol"), "symbol")
        symbol_id = _identity("symbol", symbol)
        if symbol_id not in nodes:
            symbol_count += 1
            self._check_symbol_limit(symbol_count)
        nodes[symbol_id] = CodeNode(
            id=symbol_id,
            labels=frozenset({"Symbol"}),
            properties={
                **common,
                "symbol": symbol,
                "name": _optional_string(symbol_info.get("display_name")),
                "kind": symbol_info.get("kind", 0),
                "enclosing_symbol": _optional_string(
                    symbol_info.get("enclosing_symbol")
                ),
            },
        )
        relationships = _sequence(symbol_info.get("relationships", ()), "relationships")
        for relationship_index, raw_relationship in enumerate(relationships):
            relationship = _mapping(raw_relationship, "relationships[]")
            target_symbol = _required_string(
                relationship.get("symbol"), "relationship.symbol"
            )
            target_id = _identity("symbol", target_symbol)
            if target_id not in nodes:
                symbol_count += 1
                self._check_symbol_limit(symbol_count)
                nodes[target_id] = CodeNode(
                    id=target_id,
                    labels=frozenset({"Symbol"}),
                    properties={
                        **{
                            key: value
                            for key, value in common.items()
                            if key not in {"file_path", "language"}
                        },
                        "symbol": target_symbol,
                    },
                )
            for edge_type, field in (
                ("REFERENCES", "is_reference"),
                ("IMPLEMENTS", "is_implementation"),
                ("TYPE_DEFINITION", "is_type_definition"),
                ("DEFINES", "is_definition"),
            ):
                if relationship.get(field) is not True:
                    continue
                relationship_count += 1
                if relationship_count > self._limits.relationship_limit:
                    raise ValueError("SCIP relationship limit exceeded")
                edge_id = _identity(
                    "relationship",
                    f"{symbol}:{relationship_index}:{target_symbol}:{edge_type}",
                )
                edges[edge_id] = CodeEdge(
                    id=edge_id,
                    source_id=symbol_id,
                    target_id=target_id,
                    type=edge_type,
                    properties=dict(common),
                )
        return symbol_count, relationship_count

    def _check_symbol_limit(self, count: int) -> None:
        if count > self._limits.symbol_limit:
            raise ValueError("SCIP symbol limit exceeded")

    def _occurrence_edge_types(self, roles: int) -> tuple[str, ...]:
        edge_types = []
        if roles & self.DEFINITION_ROLE:
            edge_types.append("DEFINES")
        if roles & self.IMPORT_ROLE:
            edge_types.append("IMPORTS")
        return tuple(edge_types or ["REFERENCES"])


def _identity(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"scip:{kind}:{digest}"


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _optional_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _mapping(value, name)


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    return value


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _range_value(occurrence: Mapping[str, Any]) -> Any:
    if "range" in occurrence:
        return occurrence["range"]
    return occurrence.get("TypedRange")


def _relative_path(value: Any) -> str:
    path = _required_string(value, "relative_path")
    parts = path.split("/")
    if path.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("relative_path must be a canonical relative path")
    return path
