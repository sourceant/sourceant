import difflib
import json
from pathlib import Path

from src.core.code_index import InMemoryCodeIndex
from src.core.code_index.models import CodeEdge, CodeNode
from src.core.review.call_context import GRAPH, NAMED, call_context
from src.core.scope import Scope

CAPTURED = json.loads(
    (
        Path(__file__).parents[1] / "fixtures/review-contracts/indexed-calls.json"
    ).read_text()
)
REPOSITORY = "sourceant/sourceant"


def edited():
    """The captured file with one line of `_key` changed."""
    before = CAPTURED["content"]
    after = before.replace(
        "    return keyed(repository, str(number))",
        "    return keyed(repository.lower(), str(number))",
    )
    assert after != before
    path = CAPTURED["path"]
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            f"a/{path}",
            f"b/{path}",
        )
    )
    return f"diff --git a/{path} b/{path}\n" + diff, {path: after}


def index():
    scope = Scope.from_mapping({"repository": REPOSITORY})
    stored = InMemoryCodeIndex()
    for node in CAPTURED["nodes"]:
        stored.put_node(
            scope,
            CodeNode(node["id"], frozenset(node["labels"]), node["properties"]),
        )
    for number, edge in enumerate(CAPTURED["edges"]):
        stored.put_edge(
            scope,
            CodeEdge(f"edge-{number}", edge["source"], edge["target"], edge["type"]),
        )
    return stored


def callers(context):
    key = next(key for key in context.callers if key[1] == "_key")
    return {(one.name, one.basis) for one in context.callers[key]}


def test_indexed_callers_are_preferred_and_labelled():
    diff, files = edited()
    context = call_context(diff, REPOSITORY, files.get, reader=index())
    expected = {
        edge["source"].rsplit(".", 1)[1]
        for edge in CAPTURED["edges"]
        if edge["type"] == "CALLS"
    }
    assert callers(context) == {(name, GRAPH) for name in expected}


def test_without_an_index_the_same_callers_are_matched_by_name():
    diff, files = edited()
    context = call_context(diff, REPOSITORY, files.get)
    expected = {
        edge["source"].rsplit(".", 1)[1]
        for edge in CAPTURED["edges"]
        if edge["type"] == "CALLS"
    }
    assert callers(context) == {(name, NAMED) for name in expected}
