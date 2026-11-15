import pytest
from src.core.review.planning import build_chunks, ReviewCapacityError


def added(path, lines):
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
        + "".join("+" + line + "\n" for line in lines)
    )


def test_large_single_hunk_is_split_before_any_model_request():
    result = build_chunks(
        added("main.py", ["value = 1"] * 300), cost=len, input_budget=400
    )
    assert len(result) > 1
    assert all(chunk.input_tokens <= 400 for chunk in result)
    assert (
        sum(file._patched_file.added for chunk in result for file in chunk.parsed_files)
        == 300
    )


def test_model_data_has_explicit_artifact_disposition():
    result = build_chunks(
        added("data.tiktoken", ["token value"] * 1000)
        + added("loader.py", ["load_tokens()"]),
        cost=len,
        input_budget=400,
    )
    assert all(
        file.file_path == "loader.py" for chunk in result for file in chunk.parsed_files
    )


def test_impossible_capacity_fails_without_model_calls():
    with pytest.raises(ReviewCapacityError):
        build_chunks(
            added("main.py", ["value = 1"] * 300),
            cost=len,
            input_budget=400,
            maximum_parts=1,
        )


def test_many_small_files_can_share_a_chunk():
    diff = "".join(added(f"file{index}.py", ["value = 1"]) for index in range(246))
    result = build_chunks(diff, cost=len, input_budget=4000, maximum_parts=20)
    assert len(result) <= 20
    assert (
        len({file.file_path for chunk in result for file in chunk.parsed_files}) == 246
    )
    assert sum(len(chunk.parsed_files) for chunk in result) == 246


def test_dependency_neighbours_are_packed_together():
    diff = (
        added("a.py", ["a = 1"]) + added("b.py", ["b = 1"]) + added("z.py", ["z = 1"])
    )
    result = build_chunks(
        diff, cost=len, input_budget=250, adjacency={"a.py": {"z.py"}, "z.py": {"a.py"}}
    )
    assert [file.file_path for file in result[0].parsed_files] == ["a.py", "z.py"]


def test_artifact_checks_validate_token_table_and_report_provenance_gap():
    from src.core.review.planning import artifact_checks

    check = artifact_checks(
        added("table.tiktoken", ["YQ== 0", "Yg== 1"])
        + added("loader.py", ["load('table.tiktoken')"])
    )[0]
    assert check["integrity_verified"]
    assert check["changed_consumers"] == ("loader.py",)
    assert check["gaps"] == ("Artifact provenance has not been verified",)
    assert not artifact_checks(added("table.tiktoken", ["YQ== 0", "Yg== 0"]))[0][
        "integrity_verified"
    ]


def test_deleted_source_is_kept_in_model_input():
    from src.utils.diff_parser import parse_diff

    diff = "diff --git a/deleted.py b/deleted.py\ndeleted file mode 100644\n--- a/deleted.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def validate_user():\n-    require_authentication()\n"
    file = parse_diff(diff)[0]
    assert "require_authentication()" in file.to_decoupled_format()
    assert "[file deleted]" in file.to_decoupled_format()


def test_exact_chunk_context_does_not_repeat_already_supplied_changed_lines():
    from src.core.review.execution import ReviewInvestigation, ReviewTarget
    from src.core.review.investigations import target_source

    source = "value = 1\n" * 300
    result = build_chunks(
        added("main.py", source.splitlines()), cost=len, input_budget=400
    )
    chunk = result[0]
    file = chunk.parsed_files[0]
    task = ReviewInvestigation(
        "first",
        (ReviewTarget(file.file_path, tuple(file.hunk_ranges)),),
        "Inspect this change",
        file.diff_text,
    )
    text, missing = target_source(task, lambda path: source, 1000)
    assert not missing
    first_changed_line = file.hunk_ranges[0][2]
    assert f"{first_changed_line}: value = 1" not in text.splitlines()
    assert len(text) <= 1000


def test_summary_reduction_compacts_large_records_before_model_calls():
    import json
    from types import SimpleNamespace
    from src.models.code_review import CodeReviewSummary
    from src.plugins.builtin.code_reviewer.overview import _summary_groups

    descriptions = [
        CodeReviewSummary(
            overview="A long summary " * 2000, minor_suggestions=[], critical_issues=[]
        )
        for _ in range(8)
    ]
    groups = _summary_groups(descriptions, SimpleNamespace(count_tokens=len), 2000)
    assert len(groups) < len(descriptions)
    assert all(
        len(json.dumps({"parts_of_the_same_pull_request": group})) <= 2000
        for group in groups
    )
    assert all(
        record["omitted_summary_details"] for group in groups for record in group
    )


def test_unavailable_optional_graph_falls_back_to_path_chunk_planning():
    from src.core.code_index import CodeIndexReader
    from src.core.review.planning import relationships
    from src.core.scope import Scope
    from src.core.services import ServiceRegistry

    class UnavailableGraph:
        def search(self, query):
            raise RuntimeError("Graph unavailable")

        def traverse(self, query):
            raise RuntimeError("Graph unavailable")

        def graph(self, query):
            raise RuntimeError("Graph unavailable")

    services = ServiceRegistry()
    services.register(CodeIndexReader, UnavailableGraph(), "test")
    assert (
        relationships(
            services,
            Scope.from_mapping({"repository": "owner/repo", "revision": "head"}),
        )
        == {}
    )


def test_combined_evaluation_evidence_respects_serialized_budget():
    import json
    from src.core.review.evaluation import _bounded_evidence

    pieces = [added(f"file{index}.py", ["value = 1"] * 10) for index in range(20)]
    result = _bounded_evidence(pieces, len, 1000)
    assert result
    assert len(json.dumps(result)) <= 1000
    assert len(result) < len("\n".join(pieces))


def test_planner_returns_full_chunks_that_persist_without_a_second_model(tmp_path):
    from dataclasses import replace
    from sqlalchemy import create_engine
    from src.core.review import ReviewChunk
    from src.core.review.work_store import SQLReviewWork

    chunks = build_chunks(
        added("auth/session.py", ["value = 1"] * 100),
        review_id="review",
        cost=len,
        input_budget=400,
    )
    chunks = tuple(replace(chunk, group="auth/session") for chunk in chunks)
    assert chunks and all(type(chunk) is ReviewChunk for chunk in chunks)
    store = SQLReviewWork(
        create_engine("sqlite:///" + str(tmp_path / "chunks.sqlite")),
        create_schema=True,
    )
    store.open(
        "review",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={},
        maximum_calls=64,
        chunks=chunks,
    )
    planned = {chunk.id: chunk for chunk in chunks}
    assert {chunk.id: chunk for chunk in store.chunks("review")} == planned
    for chunk in chunks:
        lease = store.claim("review", chunk.id)
        assert store.finish("review", chunk.id, lease, {"review": None, "coverage": []})
    assert all(
        chunk.status == "done" and chunk.attempts == 1
        for chunk in store.chunks("review")
    )
    assert store.complete("review")
