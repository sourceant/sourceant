from __future__ import annotations

from src.core.code_index import CodeIndexReader, CodeSearch, CodeTraversal
from src.core.scope import Scope

from .interfaces import ChangedFileEvidenceReader
from .models import FileEvidence, StructuralFact, StructuralPredicate


class IndexedChangedFileEvidenceReader:
    def __init__(
        self,
        reader: CodeIndexReader,
        scope: Scope,
        *,
        node_limit: int = 100,
    ) -> None:
        if not 2 <= node_limit <= 100:
            raise ValueError("node_limit must be between 2 and 100")
        self._reader = reader
        self._scope = scope
        self._node_limit = node_limit
        self._cache: dict[str, FileEvidence | None] = {}

    def read(self, path: str) -> FileEvidence | None:
        if path not in self._cache:
            try:
                self._cache[path] = self._read(path)
            except (OSError, RuntimeError, ValueError):
                self._cache[path] = None
        return self._cache[path]

    def _read(self, path: str) -> FileEvidence | None:
        result = self._reader.search(
            CodeSearch(
                scope=self._scope,
                labels=frozenset({"File"}),
                properties={
                    "file_path": path,
                    "revision": self._scope.get("revision"),
                    "source": "scip",
                },
                limit=1,
            )
        )
        if not result.nodes:
            return None
        file_node = result.nodes[0]
        traversal = self._reader.traverse(
            CodeTraversal(
                scope=self._scope,
                node_ids=(file_node.id,),
                depth=1,
                edge_types=frozenset({"DEFINES", "IMPORTS"}),
                direction="outbound",
                node_limit=self._node_limit,
            )
        )
        nodes = {node.id: node for node in traversal.nodes}
        facts: set[StructuralFact] = set()
        for edge in traversal.edges:
            if edge.source_id != file_node.id:
                continue
            symbol = nodes.get(edge.target_id)
            if symbol is None:
                continue
            name = symbol.properties.get("name")
            if not isinstance(name, str) or not name:
                continue
            if edge.type == "IMPORTS":
                facts.add(StructuralFact(name, StructuralPredicate.IMPORTED))
                facts.add(StructuralFact(name, StructuralPredicate.DEFINED))
            elif edge.type == "DEFINES":
                facts.add(StructuralFact(name, StructuralPredicate.DEFINED))
        language = file_node.properties.get("language")
        return FileEvidence(
            path=path,
            language=language if isinstance(language, str) else "",
            facts=frozenset(facts),
            supported_predicates=frozenset(
                {StructuralPredicate.IMPORTED, StructuralPredicate.DEFINED}
            ),
        )


class FallbackChangedFileEvidenceReader:
    def __init__(
        self,
        primary: ChangedFileEvidenceReader,
        fallback: ChangedFileEvidenceReader,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def read(self, path: str) -> FileEvidence | None:
        return self._primary.read(path) or self._fallback.read(path)
