import difflib
import json
from pathlib import Path

import pytest

from src.core.code_index import (
    CodeSearch,
    CodeTraversal,
    InMemoryCodeIndex,
    ScipJsonImporter,
)
from src.core.review.call_context import GRAPH, call_context
from src.core.scope import Scope

FIXTURES = Path(__file__).parents[1] / "fixtures" / "scip"
SOURCES = json.loads((FIXTURES / "calls-sources.json").read_text())
# Each index was written by the real indexer over the matching project in
# calls-sources.json, where one file's caller calls the other file's callee.
CASES = {
    "typescript": ("src/lib.ts", "src/main.ts", "callee", "caller"),
    "python": ("pkg/lib.py", "pkg/main.py", "callee", "caller"),
    "go": ("lib.go", "main.go", "Callee", "Caller"),
}
SCOPE = Scope.from_mapping({"repository": "example/probe", "revision": "abc123"})


def imported(language):
    index = InMemoryCodeIndex()
    ScipJsonImporter(index).import_json(
        SCOPE, (FIXTURES / f"calls-{language}.json").read_bytes()
    )
    return index


def defined(index, name):
    found = [
        node
        for node in index.search(CodeSearch(scope=SCOPE, limit=100)).nodes
        if node.properties.get("name") == name and node.properties.get("start_line")
    ]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("language", sorted(CASES))
def test_enclosing_ranges_become_calls_between_definitions(language):
    callee_path, caller_path, callee, caller = CASES[language]
    index = imported(language)
    called, calling = defined(index, callee), defined(index, caller)
    assert called.properties["file_path"] == callee_path
    assert calling.properties["file_path"] == caller_path
    near = index.traverse(
        CodeTraversal(
            scope=SCOPE,
            node_ids=(called.id,),
            depth=1,
            edge_types=frozenset({"CALLS"}),
            direction="inbound",
            node_limit=10,
        )
    )
    assert {node.id for node in near.nodes} == {called.id, calling.id}


@pytest.mark.parametrize("language", sorted(CASES))
def test_review_callers_come_from_a_supplied_index(language):
    callee_path, _, callee, caller = CASES[language]
    before = SOURCES[language][callee_path]
    after = before.replace("1", "2", 1)
    assert after != before
    diff = f"diff --git a/{callee_path} b/{callee_path}\n" + "".join(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            f"a/{callee_path}",
            f"b/{callee_path}",
        )
    )
    files = {**SOURCES[language], callee_path: after}
    context = call_context(
        diff, "example/probe", files.get, revision="abc123", reader=imported(language)
    )
    key = next(key for key in context.callers if key[1] == callee)
    assert {(one.name, one.basis) for one in context.callers[key]} == {(caller, GRAPH)}
