from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from src.core.code_index import (
    CodeEdge,
    CodeIndexReader,
    CodeNode,
    CodeSearch,
    CodeSearchResult,
    CodeTraversal,
    CodeTraversalResult,
    InMemoryCodeIndex,
)
from src.core.language_pack import Error, ProcessConfig, detect_language, process
from src.core.scope import Scope


@dataclass(frozen=True)
class ReviewCodeContext:
    content: str
    truncated: bool


class LazyChangedFileCodeIndex:
    def __init__(
        self,
        scope: Scope,
        paths: list[str],
        read_content: Callable[[str], str | None],
        *,
        file_limit: int = 20,
    ) -> None:
        if not 1 <= file_limit <= 100:
            raise ValueError("file_limit must be between 1 and 100")
        self._scope = scope
        self._paths = [
            path
            for path in dict.fromkeys(paths)
            if path and detect_language(path) is not None
        ][:file_limit]
        self._read_content = read_content
        self._index: InMemoryCodeIndex | None = None

    def search(self, query: CodeSearch) -> CodeSearchResult:
        return self._resolve().search(query)

    def traverse(self, traversal: CodeTraversal) -> CodeTraversalResult:
        return self._resolve().traverse(traversal)

    def _resolve(self) -> InMemoryCodeIndex:
        if self._index is None:
            self._index = build_changed_file_code_index(
                self._scope, self._paths, self._read_content
            )
        return self._index


class DefaultReviewCodeContextPreparer:
    _ALLOWED_PROPERTIES = frozenset(
        {
            "end_line",
            "file_path",
            "kind",
            "line",
            "name",
            "signature",
            "start_line",
            "trace_direction",
        }
    )

    def __init__(
        self,
        reader: CodeIndexReader,
        *,
        read_content: Callable[[str], str | None] | None = None,
        file_limit: int = 20,
        node_limit: int = 30,
        depth: int = 2,
        character_limit: int = 8_000,
    ) -> None:
        if not 1 <= file_limit <= 100:
            raise ValueError("file_limit must be between 1 and 100")
        if not 1 <= node_limit <= 100:
            raise ValueError("node_limit must be between 1 and 100")
        if not 1 <= depth <= 5:
            raise ValueError("depth must be between 1 and 5")
        if character_limit < 100:
            raise ValueError("character_limit must be at least 100")
        self._reader = reader
        self._read_content = read_content
        self._file_limit = file_limit
        self._node_limit = node_limit
        self._depth = depth
        self._character_limit = character_limit

    def prepare(
        self,
        *,
        repository: str,
        revision: str,
        paths: list[str],
    ) -> ReviewCodeContext | None:
        unique_paths = tuple(dict.fromkeys(path for path in paths if path))
        if not repository or not revision or not unique_paths:
            return None
        selected_paths = unique_paths[: self._file_limit]
        scope = Scope.from_mapping({"repository": repository, "revision": revision})
        seeds = []
        search_truncated = len(unique_paths) > len(selected_paths)
        for path in selected_paths:
            result = self._reader.search(
                CodeSearch(scope=scope, properties={"file_path": path}, limit=1)
            )
            seeds.extend(result.nodes)
            search_truncated = search_truncated or result.has_more
        seed_ids = tuple(dict.fromkeys(node.id for node in seeds))
        if not seed_ids:
            return None
        traversal = self._reader.traverse(
            CodeTraversal(
                scope=scope,
                node_ids=seed_ids,
                depth=self._depth,
                node_limit=self._node_limit,
            )
        )
        payload = {
            "nodes": [self._node(node) for node in traversal.nodes],
            "edges": [
                {
                    "source": edge.source_id,
                    "target": edge.target_id,
                    "type": edge.type,
                }
                for edge in traversal.edges
            ],
            "source_excerpts": self._source_excerpts(traversal),
            "truncated": search_truncated or traversal.truncated,
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(content) > self._character_limit:
            content = self._fit_to_character_limit(payload)
        truncated = payload["truncated"]
        return ReviewCodeContext(content=content, truncated=truncated)

    def _source_excerpts(self, traversal: CodeTraversalResult) -> list[dict]:
        if self._read_content is None:
            return []
        nodes = {node.id: node for node in traversal.nodes}
        locations: set[tuple[str, int, int]] = set()
        for edge in traversal.edges:
            if edge.type != "DEFINES":
                continue
            target = nodes.get(edge.target_id)
            path = edge.properties.get("file_path")
            lines = _line_range(edge.properties.get("range"))
            if target is not None:
                path = path or target.properties.get("file_path")
                if lines is None:
                    start = target.properties.get("start_line")
                    end = target.properties.get("end_line")
                    if isinstance(start, int) and isinstance(end, int):
                        lines = start, end
            if isinstance(path, str) and path and lines is not None:
                locations.add((path, *lines))

        excerpts = []
        for path, start, end in sorted(locations):
            try:
                content = self._read_content(path)
            except (OSError, RuntimeError, ValueError):
                continue
            if not isinstance(content, str):
                continue
            source_lines = content.splitlines()
            if not source_lines or start < 1 or start > len(source_lines):
                continue
            bounded_end = min(end, len(source_lines), start + 79)
            excerpt_lines = source_lines[start - 1 : bounded_end]
            total_length = sum(len(line) for line in excerpt_lines) + max(
                0, len(excerpt_lines) - 1
            )
            while total_length > 2_000 and excerpt_lines:
                removed = excerpt_lines.pop()
                total_length -= len(removed)
                if excerpt_lines:
                    total_length -= 1
            if not excerpt_lines:
                continue
            bounded_end = start + len(excerpt_lines) - 1
            excerpts.append(
                {
                    "file_path": path,
                    "start_line": start,
                    "end_line": bounded_end,
                    "content": "\n".join(excerpt_lines),
                }
            )
        return excerpts

    def _fit_to_character_limit(self, payload: dict) -> str:
        payload["truncated"] = True
        while True:
            content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if len(content) <= self._character_limit:
                return content
            if payload["nodes"]:
                payload["nodes"].pop()
                included = {node["id"] for node in payload["nodes"]}
                payload["edges"] = [
                    edge
                    for edge in payload["edges"]
                    if edge["source"] in included and edge["target"] in included
                ]
            elif len(payload["source_excerpts"]) > 1:
                payload["source_excerpts"].pop()
            elif payload["edges"]:
                payload["edges"].pop()
            else:
                payload["source_excerpts"].clear()

    @classmethod
    def _node(cls, node: CodeNode) -> dict:
        properties = {
            key: value
            for key, value in node.properties.items()
            if key in cls._ALLOWED_PROPERTIES
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        return {
            "id": node.id,
            "labels": sorted(node.labels),
            "properties": properties,
        }


def merge_review_code_contexts(
    contexts: list[ReviewCodeContext], *, character_limit: int = 8_000
) -> ReviewCodeContext | None:
    if not contexts:
        return None
    payload = {"nodes": [], "edges": [], "source_excerpts": [], "truncated": False}
    seen = {"nodes": set(), "edges": set(), "source_excerpts": set()}
    for context in contexts:
        decoded = json.loads(context.content)
        payload["truncated"] = payload["truncated"] or context.truncated
        for key in ("nodes", "edges", "source_excerpts"):
            for item in decoded.get(key, []):
                identity = json.dumps(item, sort_keys=True, separators=(",", ":"))
                if identity not in seen[key]:
                    seen[key].add(identity)
                    payload[key].append(item)
    while True:
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(content) <= character_limit:
            return ReviewCodeContext(content, payload["truncated"])
        payload["truncated"] = True
        if payload["nodes"]:
            payload["nodes"].pop()
            included = {node["id"] for node in payload["nodes"]}
            payload["edges"] = [
                edge
                for edge in payload["edges"]
                if edge["source"] in included and edge["target"] in included
            ]
        elif len(payload["source_excerpts"]) > 1:
            payload["source_excerpts"].pop()
        elif payload["edges"]:
            payload["edges"].pop()
        elif payload["source_excerpts"]:
            payload["source_excerpts"].clear()
        else:
            return ReviewCodeContext(content, True)


def _line_range(value) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) == 3 and all(isinstance(item, int) for item in value):
        return value[0] + 1, value[0] + 1
    if len(value) == 4 and all(isinstance(item, int) for item in value):
        return value[0] + 1, value[2] + 1
    return None


def build_changed_file_code_index(
    scope: Scope,
    paths: list[str],
    read_content: Callable[[str], str | None],
    *,
    character_limit: int = 500_000,
) -> InMemoryCodeIndex:
    index = InMemoryCodeIndex()
    for path in dict.fromkeys(paths):
        language = detect_language(path)
        if language is None:
            continue
        try:
            content = read_content(path)
        except (OSError, RuntimeError, ValueError):
            continue
        if not isinstance(content, str) or len(content) > character_limit:
            continue
        try:
            result = process(content, ProcessConfig(language=language))
        except (Error, RuntimeError):
            continue
        file_id = f"file:{path}"
        index.put_node(
            scope,
            CodeNode(
                file_id,
                frozenset({"File"}),
                {
                    "file_path": path,
                    "kind": language,
                    "name": path.rsplit("/", 1)[-1],
                },
            ),
        )
        for position, item in enumerate(result.imports):
            import_id = f"import:{path}:{position}"
            index.put_node(
                scope,
                CodeNode(
                    import_id,
                    frozenset({"Import"}),
                    {"file_path": path, "name": item.source},
                ),
            )
            index.put_edge(
                scope,
                CodeEdge(
                    f"imports:{file_id}:{import_id}",
                    file_id,
                    import_id,
                    "IMPORTS",
                ),
            )
        _put_structure(index, scope, path, file_id, result.structure)
    return index


def _put_structure(index, scope, path, parent_id, items) -> None:
    for position, item in enumerate(items):
        if not item.name:
            _put_structure(index, scope, path, parent_id, item.children)
            continue
        symbol_id = f"symbol:{path}:{item.name}:{item.span.start_line}:{position}"
        index.put_node(
            scope,
            CodeNode(
                symbol_id,
                frozenset({str(item.kind)}),
                {
                    "file_path": path,
                    "kind": str(item.kind),
                    "name": item.name,
                    "signature": item.signature,
                    "start_line": item.span.start_line + 1,
                    "end_line": item.span.end_line + 1,
                },
            ),
        )
        index.put_edge(
            scope,
            CodeEdge(
                f"defines:{parent_id}:{symbol_id}",
                parent_id,
                symbol_id,
                "DEFINES",
            ),
        )
        _put_structure(index, scope, path, symbol_id, item.children)
