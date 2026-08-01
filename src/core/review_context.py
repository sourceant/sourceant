from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from tree_sitter_language_pack import Error, ProcessConfig, detect_language, process

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
            "truncated": search_truncated or traversal.truncated,
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(content) > self._character_limit:
            content = self._fit_to_character_limit(payload)
        truncated = payload["truncated"]
        return ReviewCodeContext(content=content, truncated=truncated)

    def _fit_to_character_limit(self, payload: dict) -> str:
        payload["truncated"] = True
        nodes = payload["nodes"]
        edges = payload["edges"]
        lower = 0
        upper = len(nodes)
        content = ""
        while lower <= upper:
            count = (lower + upper) // 2
            candidate_nodes = nodes[:count]
            included = {node["id"] for node in candidate_nodes}
            candidate = {
                "nodes": candidate_nodes,
                "edges": [
                    edge
                    for edge in edges
                    if edge["source"] in included and edge["target"] in included
                ],
                "truncated": True,
            }
            serialized = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
            if len(serialized) <= self._character_limit:
                content = serialized
                lower = count + 1
            else:
                upper = count - 1
        kept = max(0, lower - 1)
        payload["nodes"] = nodes[:kept]
        included = {node["id"] for node in payload["nodes"]}
        payload["edges"] = [
            edge
            for edge in edges
            if edge["source"] in included and edge["target"] in included
        ]
        return content

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
        except Error:
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
