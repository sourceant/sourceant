import json

import pytest

from src.core.review_context import (
    DefaultReviewCodeContextPreparer,
    LazyChangedFileCodeIndex,
    ReviewCodeContext,
    build_changed_file_code_index,
    merge_review_code_contexts,
)
from src.core.code_index import CodeEdge, CodeNode, CodeSearch, InMemoryCodeIndex
from src.core.scope import Scope
from src.utils.diff_parser import parse_diff


def test_changed_file_graph_exposes_symbols_and_imports():
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    index = build_changed_file_code_index(
        scope,
        ["src/service.py"],
        lambda path: (
            "import logging\n\n"
            "def helper():\n"
            "    return 1\n\n"
            "def run():\n"
            "    return helper()\n"
        ),
    )

    context = DefaultReviewCodeContextPreparer(index).prepare(
        repository="sourceant/sourceant",
        revision="abc123",
        paths=["src/service.py"],
    )

    assert context is not None
    payload = json.loads(context.content)
    assert {node["properties"].get("name") for node in payload["nodes"]} == {
        "service.py",
        "helper",
        "run",
        "import logging",
    }
    assert any(edge["type"] == "IMPORTS" for edge in payload["edges"])


@pytest.mark.parametrize(
    ("path", "content", "symbol"),
    [
        ("src/service.py", "def run(): return 1", "run"),
        ("src/service.js", "function run() { return 1; }", "run"),
        ("src/service.ts", "export function run(): number { return 1; }", "run"),
        ("src/service.php", "<?php function run() { return 1; }", "run"),
    ],
)
def test_changed_file_graph_uses_one_parser_contract_across_languages(
    path, content, symbol
):
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    index = build_changed_file_code_index(scope, [path], lambda candidate: content)

    context = DefaultReviewCodeContextPreparer(index).prepare(
        repository="sourceant/sourceant",
        revision="abc123",
        paths=[path],
    )

    assert context is not None
    names = {
        node["properties"].get("name") for node in json.loads(context.content)["nodes"]
    }
    assert symbol in names


def test_review_context_is_bounded_and_marks_truncation():
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    index = build_changed_file_code_index(
        scope,
        ["src/service.py"],
        lambda path: "\n".join(f"def function_{item}(): pass" for item in range(20)),
    )

    context = DefaultReviewCodeContextPreparer(
        index, node_limit=5, character_limit=10_000
    ).prepare(
        repository="sourceant/sourceant",
        revision="abc123",
        paths=["src/service.py"],
    )

    assert context is not None
    assert len(json.loads(context.content)["nodes"]) == 5
    assert context.truncated is True


def test_review_context_character_limit_keeps_a_valid_bounded_graph():
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    index = build_changed_file_code_index(
        scope,
        ["src/service.py"],
        lambda path: "\n".join(
            f"def function_with_a_long_name_{item}(): pass" for item in range(20)
        ),
    )

    context = DefaultReviewCodeContextPreparer(
        index,
        node_limit=30,
        character_limit=500,
    ).prepare(
        repository="sourceant/sourceant",
        revision="abc123",
        paths=["src/service.py"],
    )

    assert context is not None
    assert len(context.content) <= 500
    payload = json.loads(context.content)
    node_ids = {node["id"] for node in payload["nodes"]}
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in payload["edges"]
    )
    assert payload["truncated"] is True


def test_durable_reference_graph_includes_definition_source():
    scope = Scope.from_mapping({"repository": "flatrun/agent", "revision": "abc123"})
    index = InMemoryCodeIndex()
    changed = CodeNode(
        "file:server.go", frozenset({"File"}), {"file_path": "server.go"}
    )
    definition = CodeNode(
        "file:templates.go", frozenset({"File"}), {"file_path": "templates.go"}
    )
    symbol = CodeNode(
        "symbol:templates.List",
        frozenset({"Symbol"}),
        {"name": "List"},
    )
    for node in (changed, definition, symbol):
        index.put_node(scope, node)
    index.put_edge(
        scope,
        CodeEdge("reference", changed.id, symbol.id, "REFERENCES"),
    )
    index.put_edge(
        scope,
        CodeEdge(
            "definition",
            definition.id,
            symbol.id,
            "DEFINES",
            {"file_path": "templates.go", "range": [0, 0, 3, 1]},
        ),
    )
    files = {
        "templates.go": (
            "func List() []string {\n"
            '    return []string{"infra/postgres", "welcome"}\n'
            "}\n"
            "\n"
        )
    }

    context = DefaultReviewCodeContextPreparer(index, read_content=files.get).prepare(
        repository="flatrun/agent",
        revision="abc123",
        paths=["server.go"],
    )

    assert context is not None
    excerpts = json.loads(context.content)["source_excerpts"]
    assert excerpts == [
        {
            "content": (
                "func List() []string {\n"
                '    return []string{"infra/postgres", "welcome"}\n'
                "}\n"
            ),
            "end_line": 4,
            "file_path": "templates.go",
            "start_line": 1,
        }
    ]


def test_review_context_merges_durable_and_pr_head_evidence():
    durable = ReviewCodeContext(
        json.dumps(
            {
                "nodes": [{"id": "durable", "labels": [], "properties": {}}],
                "edges": [],
                "source_excerpts": [{"file_path": "dependency.go", "content": "body"}],
                "truncated": False,
            }
        ),
        False,
    )
    local = ReviewCodeContext(
        json.dumps(
            {
                "nodes": [{"id": "local", "labels": [], "properties": {}}],
                "edges": [],
                "source_excerpts": [],
                "truncated": False,
            }
        ),
        False,
    )

    merged = merge_review_code_contexts([durable, local])

    assert merged is not None
    payload = json.loads(merged.content)
    assert {node["id"] for node in payload["nodes"]} == {"durable", "local"}
    assert payload["source_excerpts"][0]["file_path"] == "dependency.go"


def test_review_context_skips_definition_with_invalid_start_line():
    scope = Scope.from_mapping({"repository": "example/repo", "revision": "abc123"})
    index = InMemoryCodeIndex()
    file_node = CodeNode(
        "file:service.py", frozenset({"File"}), {"file_path": "service.py"}
    )
    symbol = CodeNode("symbol:run", frozenset({"Symbol"}), {"name": "run"})
    index.put_node(scope, file_node)
    index.put_node(scope, symbol)
    index.put_edge(
        scope,
        CodeEdge(
            "definition",
            file_node.id,
            symbol.id,
            "DEFINES",
            {"file_path": "service.py", "range": [-2, 0, -1, 1]},
        ),
    )

    context = DefaultReviewCodeContextPreparer(
        index, read_content=lambda path: "def run(): pass\n"
    ).prepare(
        repository="example/repo",
        revision="abc123",
        paths=["service.py"],
    )

    assert context is not None
    assert json.loads(context.content)["source_excerpts"] == []


def test_changed_file_index_reads_files_only_when_queried():
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    reads = []
    index = LazyChangedFileCodeIndex(
        scope,
        ["src/service.py"],
        lambda path: reads.append(path) or "def run(): return 1",
    )

    assert reads == []

    index.search(CodeSearch(scope=scope, properties={"file_path": "src/service.py"}))

    assert reads == ["src/service.py"]


def test_changed_file_index_skips_unsupported_files_before_reading():
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    reads = []
    index = LazyChangedFileCodeIndex(
        scope,
        ["assets/logo.png", "src/service.py"],
        lambda path: reads.append(path) or "def run(): return 1",
    )

    index.search(CodeSearch(scope=scope, properties={"file_path": "src/service.py"}))

    assert reads == ["src/service.py"]


def test_binary_diff_is_identified_without_reading_repository_content():
    parsed = parse_diff(
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "index 1234567..89abcde 100644\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ\n"
    )

    assert len(parsed) == 1
    assert parsed[0].file_path == "assets/logo.png"
    assert parsed[0].is_binary_file is True


def test_changed_file_index_caps_files_before_reading():
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    reads = []
    paths = [f"src/service_{number}.py" for number in range(25)]
    index = LazyChangedFileCodeIndex(
        scope,
        paths,
        lambda path: reads.append(path) or "def run(): return 1",
        file_limit=20,
    )

    index.search(CodeSearch(scope=scope, properties={"file_path": paths[0]}))

    assert reads == paths[:20]


def test_context_file_limit_controls_indexing_and_context_seeds():
    scope = Scope.from_mapping(
        {"repository": "sourceant/sourceant", "revision": "abc123"}
    )
    reads = []
    paths = [f"src/service_{number}.py" for number in range(25)]
    index = LazyChangedFileCodeIndex(
        scope,
        paths,
        lambda path: reads.append(path) or "def run(): return 1",
        file_limit=25,
    )

    context = DefaultReviewCodeContextPreparer(
        index,
        file_limit=25,
        node_limit=100,
        character_limit=20_000,
    ).prepare(
        repository="sourceant/sourceant",
        revision="abc123",
        paths=paths,
    )

    assert context is not None
    assert reads == paths
    file_paths = {
        node["properties"].get("file_path")
        for node in json.loads(context.content)["nodes"]
        if "File" in node["labels"]
    }
    assert file_paths == set(paths)
