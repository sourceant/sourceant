import pytest
from sqlalchemy import create_engine

from src.core.code_index import (
    CodeEdge,
    CodeGraphQuery,
    CodeNode,
    CodeSearch,
    CodeTraversal,
    InMemoryCodeIndex,
)
from src.core.code_index.sql import SQLCodeIndexRepository
from src.core.scope import Scope

SCOPE = Scope.from_mapping({"repository": "acme/billing", "revision": "abc123"})
OTHER = Scope.from_mapping({"repository": "acme/billing", "revision": "def456"})

NODES = (
    CodeNode("file:src/a.py", frozenset({"File"}), {"file_path": "src/a.py"}),
    CodeNode("file:src/b.py", frozenset({"File"}), {"file_path": "src/b.py"}),
    CodeNode(
        "file:tests/test_a.py", frozenset({"File"}), {"file_path": "tests/test_a.py"}
    ),
    CodeNode(
        "symbol:src/a.py:charge:10:0",
        frozenset({"function"}),
        {"file_path": "src/a.py", "name": "charge", "start_line": 10},
    ),
    CodeNode(
        "symbol:src/b.py:refund:4:0",
        frozenset({"function"}),
        {"file_path": "src/b.py", "name": "refund", "start_line": 4},
    ),
)

EDGES = (
    CodeEdge(
        "defines:a:charge", "file:src/a.py", "symbol:src/a.py:charge:10:0", "DEFINES"
    ),
    CodeEdge(
        "defines:b:refund", "file:src/b.py", "symbol:src/b.py:refund:4:0", "DEFINES"
    ),
    CodeEdge("imports:a:b", "file:src/a.py", "file:src/b.py", "IMPORTS"),
)


def _fill(index):
    for node in NODES:
        index.put_node(SCOPE, node)
    for edge in EDGES:
        index.put_edge(SCOPE, edge)
    return index


@pytest.fixture
def stores(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'index.db'}")
    return _fill(InMemoryCodeIndex()), _fill(
        SQLCodeIndexRepository(engine, create_schema=True)
    )


def _ids(result):
    return [node.id for node in result.nodes]


def _edge_ids(result):
    return sorted(edge.id for edge in result.edges)


def test_searching_by_file_path_matches_the_reference_index(stores):
    reference, durable = stores
    query = CodeSearch(scope=SCOPE, properties={"file_path": "src/a.py"})

    expected = reference.search(query)
    actual = durable.search(query)

    assert _ids(actual) == _ids(expected)
    assert (actual.total, actual.has_more) == (expected.total, expected.has_more)


def test_searching_by_label_matches_the_reference_index(stores):
    reference, durable = stores
    query = CodeSearch(scope=SCOPE, labels=frozenset({"File"}))

    expected = reference.search(query)
    actual = durable.search(query)

    assert _ids(actual) == _ids(expected)
    assert actual.total == expected.total


def test_searching_by_an_arbitrary_property_matches_the_reference_index(stores):
    reference, durable = stores
    query = CodeSearch(scope=SCOPE, properties={"name": "refund"})

    assert _ids(durable.search(query)) == _ids(reference.search(query))


def test_paging_matches_the_reference_index(stores):
    reference, durable = stores
    query = CodeSearch(scope=SCOPE, limit=2, offset=1)

    expected = reference.search(query)
    actual = durable.search(query)

    assert _ids(actual) == _ids(expected)
    assert actual.has_more == expected.has_more


def test_a_scope_cannot_see_another_scopes_nodes(stores):
    _, durable = stores
    durable.put_node(OTHER, CodeNode("file:src/z.py", frozenset({"File"}), {}))

    assert durable.search(CodeSearch(scope=OTHER)).total == 1


def test_traversal_matches_the_reference_index(stores):
    reference, durable = stores
    traversal = CodeTraversal(scope=SCOPE, node_ids=("file:src/a.py",), depth=2)

    expected = reference.traverse(traversal)
    actual = durable.traverse(traversal)

    assert sorted(_ids(actual)) == sorted(_ids(expected))
    assert _edge_ids(actual) == _edge_ids(expected)


def test_traversal_respects_direction_and_edge_type(stores):
    reference, durable = stores
    traversal = CodeTraversal(
        scope=SCOPE,
        node_ids=("file:src/a.py",),
        depth=1,
        direction="outbound",
        edge_types=frozenset({"IMPORTS"}),
    )

    expected = reference.traverse(traversal)
    actual = durable.traverse(traversal)

    assert sorted(_ids(actual)) == sorted(_ids(expected))
    assert _edge_ids(actual) == _edge_ids(expected)


def test_drawing_the_scope_leaves_tests_out_like_the_reference_index(stores):
    reference, durable = stores
    query = CodeGraphQuery(scope=SCOPE)

    expected = reference.graph(query)
    actual = durable.graph(query)

    assert _ids(actual) == _ids(expected)
    assert "file:tests/test_a.py" not in _ids(actual)
    assert _edge_ids(actual) == _edge_ids(expected)


def test_drawing_can_be_narrowed_to_a_path(stores):
    reference, durable = stores
    query = CodeGraphQuery(scope=SCOPE, path_prefix="src/b")

    assert _ids(durable.graph(query)) == _ids(reference.graph(query))


def test_drawing_reports_when_it_stopped_early(stores):
    reference, durable = stores
    query = CodeGraphQuery(scope=SCOPE, node_limit=2)

    expected = reference.graph(query)
    actual = durable.graph(query)

    assert actual.truncated is True
    assert len(actual.nodes) == len(expected.nodes) == 2


def test_clearing_a_scope_removes_its_nodes_edges_and_labels(stores):
    _, durable = stores

    durable.clear(SCOPE)

    assert durable.search(CodeSearch(scope=SCOPE)).total == 0
    assert durable.graph(CodeGraphQuery(scope=SCOPE)).edges == ()
    durable.put_node(SCOPE, CodeNode("file:src/a.py", frozenset({"File"}), {}))
    assert (
        durable.search(CodeSearch(scope=SCOPE, labels=frozenset({"File"}))).total == 1
    )


def test_an_edge_needs_both_ends_in_the_same_scope(stores):
    _, durable = stores

    with pytest.raises(ValueError):
        durable.put_edge(
            SCOPE, CodeEdge("dangling", "file:src/a.py", "file:missing.py", "IMPORTS")
        )


def test_a_node_written_twice_keeps_only_the_last_labels(stores):
    _, durable = stores

    durable.put_node(SCOPE, CodeNode("file:src/a.py", frozenset({"Module"}), {}))

    assert (
        durable.search(CodeSearch(scope=SCOPE, labels=frozenset({"File"}))).total == 2
    )
    assert (
        durable.search(CodeSearch(scope=SCOPE, labels=frozenset({"Module"}))).total == 1
    )


def test_a_batch_writes_the_same_graph_as_one_at_a_time(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'batch.db'}")
    durable = SQLCodeIndexRepository(engine, create_schema=True)

    with durable.bulk_writes():
        for node in NODES:
            durable.put_node(SCOPE, node)
        for edge in EDGES:
            durable.put_edge(SCOPE, edge)

    assert durable.search(CodeSearch(scope=SCOPE)).total == len(NODES)
    assert len(
        durable.graph(CodeGraphQuery(scope=SCOPE, include_tests=True)).edges
    ) == len(EDGES)


def test_a_batch_still_refuses_an_edge_with_no_node(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'batch2.db'}")
    durable = SQLCodeIndexRepository(engine, create_schema=True)

    with pytest.raises(ValueError):
        with durable.bulk_writes():
            durable.put_node(SCOPE, NODES[0])
            durable.put_edge(
                SCOPE, CodeEdge("bad", "file:src/a.py", "file:nope.py", "IMPORTS")
            )
