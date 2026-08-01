import json

import pytest

from src.core.review_context import (
    DefaultReviewCodeContextPreparer,
    LazyChangedFileCodeIndex,
    build_changed_file_code_index,
)
from src.core.code_index import CodeSearch
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
