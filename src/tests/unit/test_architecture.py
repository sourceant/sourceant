from src.core.code_index import CodeEdge, CodeGraphResult, CodeNode
from src.core.code_index.architecture import compare, summarize

import pytest


def node(identity, path):
    return CodeNode(
        identity, frozenset({"Function"}), {"file_path": path, "name": identity}
    )


def test_order_does_not_change_the_snapshot():
    nodes = tuple(node(str(number), f"part{number}/main.py") for number in range(5))
    edges = tuple(CodeEdge(str(i), "0", str(i), "CALLS") for i in range(1, 5))
    first = summarize(CodeGraphResult(nodes, edges, False), repository="repo")
    second = summarize(
        CodeGraphResult(tuple(reversed(nodes)), tuple(reversed(edges)), False),
        repository="repo",
    )
    assert first == second


def test_unplaced_nodes_and_unresolved_edges_are_visible():
    nodes = (node("one", "src/one.py"), node("bad", "../outside.py"))
    graph = CodeGraphResult(nodes, (CodeEdge("edge", "one", "bad", "CALLS"),), False)
    result = summarize(graph, repository="repo")
    assert result["coverage"]["unplaced_nodes"] == 1
    assert result["coverage"]["unresolved_edges"] == 1
    with pytest.raises(ValueError, match="Incomplete"):
        compare(result, result)


def test_relationships_and_evidence_are_bounded():
    nodes = tuple(node(str(number), f"part{number}/main.py") for number in range(1003))
    edges = tuple(CodeEdge(str(i), "0", str(i), "CALLS") for i in range(1, 1003))
    result = summarize(CodeGraphResult(nodes, edges, False), repository="repo")
    assert len(result["relationships"]) == 1000
    assert result["coverage"]["truncated"]
    with pytest.raises(ValueError, match="Incomplete"):
        compare(result, result)


def test_grouping_and_repository_must_match():
    graph = CodeGraphResult((node("one", "src/payments/main.py"),), (), False)
    before = summarize(graph, repository="repo")
    for after in (
        summarize(graph, repository="other"),
        summarize(graph, repository="repo", depth=2),
    ):
        with pytest.raises(ValueError, match="same"):
            compare(before, after)
