import json
from pathlib import Path

import pytest

from src.core.code_index import (
    CodeSearch,
    CodeTraversal,
    CodeNode,
    InMemoryCodeIndex,
    ScipImportLimits,
    ScipJsonImporter,
)
from src.core.review_evidence import IndexedChangedFileEvidenceReader
from src.core.review_evidence.models import StructuralFact, StructuralPredicate
from src.core.scope import Scope


FIXTURE = Path(__file__).parents[1] / "fixtures" / "scip" / "index-v0.9.0.json"


def test_imports_real_scip_index_into_scoped_code_graph():
    scope = Scope.from_mapping(
        {"repository": "example/service", "revision": "abc123"}
    )
    index = InMemoryCodeIndex()

    result = ScipJsonImporter(index).import_json(scope, FIXTURE.read_bytes())

    assert result.documents == 1
    assert result.symbols == 3
    assert result.occurrences == 4
    assert result.relationships == 1
    files = index.search(
        CodeSearch(
            scope=scope,
            labels=frozenset({"File"}),
            properties={"file_path": "src/service.ts", "revision": "abc123"},
            limit=1,
        )
    )
    graph = index.traverse(
        CodeTraversal(
            scope=scope,
            node_ids=(files.nodes[0].id,),
            depth=2,
            node_limit=10,
        )
    )

    assert {node.properties.get("name") for node in graph.nodes} == {
        None,
        "Service",
        "logger",
        "run",
    }
    assert {edge.type for edge in graph.edges} == {
        "DEFINES",
        "IMPORTS",
        "REFERENCES",
    }


def test_indexed_evidence_uses_only_the_requested_revision():
    payload = json.loads(FIXTURE.read_text())
    scope = Scope.from_mapping(
        {"repository": "example/service", "revision": "abc123"}
    )
    index = InMemoryCodeIndex()
    ScipJsonImporter(index).import_index(scope, payload)

    evidence = IndexedChangedFileEvidenceReader(index, scope).read("src/service.ts")
    stale = IndexedChangedFileEvidenceReader(
        index,
        Scope.from_mapping(
            {"repository": "example/service", "revision": "def456"}
        ),
    ).read("src/service.ts")

    assert evidence is not None
    assert StructuralFact("Service", StructuralPredicate.DEFINED) in evidence.facts
    assert StructuralFact("run", StructuralPredicate.DEFINED) in evidence.facts
    assert StructuralFact("logger", StructuralPredicate.IMPORTED) in evidence.facts
    assert stale is None


def test_scip_import_validates_limits_before_replacing_existing_graph():
    payload = json.loads(FIXTURE.read_text())
    scope = Scope.from_mapping(
        {"repository": "example/service", "revision": "abc123"}
    )
    index = InMemoryCodeIndex()
    index.put_node(scope, CodeNode(id="existing"))

    with pytest.raises(ValueError, match="document limit"):
        ScipJsonImporter(
            index,
            limits=ScipImportLimits(document_limit=1),
        ).import_index(scope, {**payload, "documents": payload["documents"] * 2})

    assert index.search(CodeSearch(scope=scope)).nodes == (CodeNode(id="existing"),)


def test_scip_import_requires_revision_scope():
    payload = json.loads(FIXTURE.read_text())

    with pytest.raises(ValueError, match="require a revision"):
        ScipJsonImporter(InMemoryCodeIndex()).import_index(
            Scope.from_mapping({"repository": "example/service"}), payload
        )
