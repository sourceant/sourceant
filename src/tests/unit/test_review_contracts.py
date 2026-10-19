import json
from pathlib import Path

import pytest

from src.core.change_context import ChangeSet, ChangedFile
from src.core.review.checkout import CheckoutConfiguration
from src.core.review.contracts import contract_context
from src.core.review.contracts import finding_context
from src.core.review.source_search import SnapshotSearcher
from src.core.search import Searcher
from src.core.review.evaluation import ModelReviewEvaluator
from src.core.review.evaluation import _EvaluationAnswer
from src.core.review.execution import ReviewInput
from src.core.scope import Scope
from src.models.code_review import CodeSuggestion
from src.core.review import ReviewContextProvider, ReviewSourceContext
from src.core.services import ServiceRegistry
from src.models.code_review import CodeReviewFindings
from src.utils.line_mapper import LineMapper
from src.utils.diff_parser import parse_diff

FIXTURES = Path(__file__).parents[1] / "fixtures/review-contracts"


def test_supplied_captured_source_is_not_also_reported_as_omitted():
    captured = json.loads(
        (FIXTURES / "contradictory-evidence-metadata.json").read_text()
    )
    path = captured["path"]
    sources = {**captured["candidate_source"], path: captured["source"]}
    candidates = [
        CodeSuggestion.model_validate(one["finding"]) for one in captured["candidates"]
    ]

    class Context:
        def prepare(self, request, paths):
            return ReviewSourceContext(
                source={path: sources[path]},
                omitted=captured["captured_omitted_context"],
            )

    class Model:
        token_limit = 131072
        prompt = ""

        def count_tokens(self, text):
            return len(text) // 4

        def generate_text(self, prompt, *, purpose):
            self.prompt = prompt
            return captured["response"]

    model = Model()
    services = ServiceRegistry().with_service(ReviewContextProvider, Context(), "test")
    changes = ChangeSet(
        Scope(),
        tuple(ChangedFile(name) for name in captured["candidate_source"]),
        diff=captured["diff"],
        configuration=CheckoutConfiguration(),
    )
    ModelReviewEvaluator(model, services)._evaluate(
        ReviewInput(changes, read_content=sources.get),
        candidates,
        pass_id="captured",
        indices=tuple(captured["batch_indices"]),
    )
    payload = json.loads(model.prompt.split("\nReview input:\n", 1)[1])
    assert payload["source"][path] == sources[path]
    assert not set(payload["source"]) & set(payload["omitted_context"])
    unavailable = set(captured["captured_omitted_context"]) - set(sources)
    assert unavailable <= set(payload["omitted_context"])


def test_captured_duplicate_code_keeps_anchor_near_the_reported_validator():
    captured = json.loads(
        (FIXTURES / "techdebt-evaluation-batch-input.json").read_text()
    )
    suggestion = CodeSuggestion.model_validate(captured["candidates"][0]["finding"])
    original_start = suggestion.start_line
    mapping, _ = LineMapper(parse_diff(captured["diff"])).validate_and_map_suggestion(
        suggestion, strict_mode=True
    )
    assert abs(mapping["line"] - original_start) <= 2
    assert mapping.get("start_line", mapping["line"]) <= mapping["line"]


def test_unresolved_duplicate_metadata_does_not_change_verdict_or_retry():
    answer = _EvaluationAnswer.model_validate_json(
        (FIXTURES / "techdebt-unresolved-duplicate-response.json").read_text()
    )
    unresolved = [one for one in answer.judgments if one.status.value == "unresolved"]
    assert len(unresolved) == 4
    assert all(one.duplicate_of is None for one in unresolved)


@pytest.mark.parametrize("has_ripgrep", [True, False])
def test_captured_evaluation_batch_keeps_the_input_validator_in_evidence(
    monkeypatch, has_ripgrep
):
    if not has_ripgrep:
        monkeypatch.setattr("src.core.search.source_text.shutil.which", lambda _: None)
        monkeypatch.setattr("src.core.search.source_text.SMALL_SOURCE_BYTES", 1)
    captured = json.loads(
        (FIXTURES / "techdebt-evaluation-batch-input.json").read_text()
    )
    candidates = [
        CodeSuggestion.model_validate(one["finding"]) for one in captured["candidates"]
    ]
    read = reader("techdebt-final")
    scope = Scope.from_mapping(
        {"repository": "PierreJanineh/TechDebtMCP", "revision": "16a54f0f"}
    )
    paths = tuple(
        str(path.relative_to(FIXTURES / "techdebt-final"))
        for path in (FIXTURES / "techdebt-final").rglob("*.*")
    )
    services = ServiceRegistry().with_service(
        Searcher, SnapshotSearcher(scope, paths, read), "snapshot"
    )

    class Model:
        token_limit = 131072
        prompt = ""

        def count_tokens(self, content):
            return len(content) // 4

        def generate_text(self, prompt, *, purpose):
            self.prompt = prompt
            return (FIXTURES / "techdebt-evaluation-batch-response.json").read_text()

    model = Model()
    request = ReviewInput(
        ChangeSet(
            scope,
            tuple(ChangedFile(path) for path in captured["source"]),
            diff=captured["diff"],
            configuration=CheckoutConfiguration(),
        ),
        read_content=read,
        source_context=captured["source"],
    )
    ModelReviewEvaluator(model, services)._evaluate(
        request, candidates, pass_id="captured", indices=captured["batch_indices"]
    )
    payload = json.loads(model.prompt.split("\nReview input:\n", 1)[1])
    assert payload["source"]["src/server/configValidator.ts"] == read(
        "src/server/configValidator.ts"
    )


def test_captured_finding_retrieves_actual_input_validator_before_contracts():
    candidate = CodeSuggestion.model_validate_json(
        (FIXTURES / "techdebt-numeric-flags-candidate.json").read_text()
    )
    read = reader("techdebt-final")
    scope = Scope.from_mapping(
        {"repository": "PierreJanineh/TechDebtMCP", "revision": "16a54f0f"}
    )
    paths = tuple(
        str(path.relative_to(FIXTURES / "techdebt-final"))
        for path in (FIXTURES / "techdebt-final").rglob("*.ts")
    )
    services = ServiceRegistry().with_service(
        Searcher, SnapshotSearcher(scope, paths, read), "snapshot"
    )
    request = ReviewInput(
        ChangeSet(scope, (ChangedFile(candidate.file_name),)), read_content=read
    )
    context = finding_context(request, [candidate], services)
    assert context.source["src/server/configValidator.ts"] == read(
        "src/server/configValidator.ts"
    )
    assert "isCustomPatternShape" in context.source["src/server/configValidator.ts"]


def test_captured_finding_retrieves_cited_root_guidance():
    candidate = CodeSuggestion.model_validate_json(
        (FIXTURES / "techdebt-relative-path-candidate.json").read_text()
    )
    read = reader("techdebt-final")
    request = ReviewInput(
        ChangeSet(Scope(), (ChangedFile(candidate.file_name),)), read_content=read
    )
    context = finding_context(request, [candidate], ServiceRegistry())
    assert context.source["CLAUDE.md"] == read("CLAUDE.md")
    assert set(context.source) == {"CLAUDE.md"}


def test_discovery_schema_has_one_collection_for_all_findings():
    schema = CodeReviewFindings.model_json_schema()
    assert set(schema["properties"]) == {"code_suggestions", "verdict"}
    assert "execution" not in schema["properties"]


def test_registered_context_provider_is_used_by_evaluation():
    captured = json.loads((FIXTURES / "node-postgres-controlled.json").read_text())
    original = captured["input"]
    candidates = [
        CodeSuggestion.model_validate(item["finding"])
        for item in original["candidates"]
    ]
    read = reader("node-postgres-controlled")
    calls = []

    class Context:
        def prepare(self, request, paths):
            calls.append((request.changes.scope, tuple(paths)))
            return ReviewSourceContext(
                source={"packages/pg/test/suite.js": read("packages/pg/test/suite.js")}
            )

    class Model:
        token_limit = 131072
        prompt = ""

        def count_tokens(self, content):
            return len(content) // 4

        def generate_text(self, prompt, *, purpose):
            self.prompt = prompt
            return json.loads(
                (FIXTURES / "node-postgres-controlled-full-response.json").read_text()
            )

    services = ServiceRegistry().with_service(
        ReviewContextProvider, Context(), "custom"
    )
    changes = ChangeSet(
        Scope(),
        tuple(ChangedFile(path) for path in original["source"]),
        diff=original["diff"],
        configuration=CheckoutConfiguration(),
    )
    model = Model()
    ModelReviewEvaluator(model, services).evaluate(
        ReviewInput(changes, read_content=read), candidates, pass_id="captured"
    )
    payload = json.loads(model.prompt.split("\nReview input:\n", 1)[1])
    assert len(calls) == 1
    assert "packages/pg/test/suite.js" in payload["source"]
    assert "packages/pg/package.json" not in payload["source"]


def reader(case):
    def read(path):
        target = FIXTURES / case / path
        return target.read_text() if target.is_file() else None

    return read


def test_actual_nested_test_dependencies_and_package_contracts_are_loaded():
    read = reader("node-postgres-controlled")
    context = contract_context(
        [
            "packages/pg/test/unit/client/diagnostics-tests.js",
            "packages/pg-pool/index.js",
        ],
        read,
    )
    assert "packages/pg/test/suite.js" in context.source
    assert (
        "async.queue(this.run.bind(this), 1)"
        in context.source["packages/pg/test/suite.js"]
    )
    assert context.source["packages/pg-pool/package.json"] == read(
        "packages/pg-pool/package.json"
    )
    assert '"index.js"' in context.source["packages/pg-pool/package.json"]


def test_test_harness_survives_full_candidate_dependency_budget():
    captured = json.loads((FIXTURES / "node-postgres-controlled.json").read_text())
    paths = [item["finding"]["file_name"] for item in captured["input"]["candidates"]]
    context = contract_context(paths, reader("node-postgres-controlled"))
    assert "packages/pg/test/suite.js" in context.source
    assert "packages/pg/test/test-helper.js" in context.source
    assert sum(map(len, context.source.values())) <= 60000
    assert "packages/pg/test/suite.js" not in context.omitted
    assert "packages/pg/lib/query.js" in context.source


def test_compiled_import_resolves_actual_typescript_implementation():
    context = contract_context(["src/server/handlers.ts"], reader("techdebt-final"))
    assert "src/utils/fileUtils.ts" in context.source


def captured_paths(case):
    root = FIXTURES / case
    return [
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    ]


def test_actual_repository_guidance_is_available_to_reviewers():
    from src.core.review.contracts import documentation_files

    changed = ["src/server/handlers.ts"]
    context = contract_context(
        changed,
        reader("techdebt-final"),
        guidance=documentation_files(captured_paths("techdebt-final"), changed),
    )
    guidance = context.source["CLAUDE.md"]
    assert "path.isAbsolute" in guidance
    assert "path.resolve" in guidance
    assert "getRelativePath" in guidance
    assert sum(map(len, context.source.values())) <= 60000


def test_contract_budget_reports_whole_file_omissions():
    context = contract_context(
        ["packages/pg/test/unit/client/diagnostics-tests.js"],
        reader("node-postgres-controlled"),
        max_chars=100,
    )
    assert sum(len(content) for content in context.source.values()) <= 100
    assert context.omitted
    assert "packages/pg/test/suite.js" not in context.source


def test_captured_cross_file_duplicate_response_is_accepted_without_retry():
    captured = json.loads((FIXTURES / "node-packaging-duplicates.json").read_text())
    original = captured["input"]
    candidates = [
        CodeSuggestion.model_validate(item) for item in captured["candidates"]
    ]

    class Model:
        token_limit = 131072
        calls = 0

        def count_tokens(self, content):
            return len(content) // 4

        def generate_text(self, prompt, *, purpose):
            self.calls += 1
            return captured["response"]

    model = Model()
    changes = ChangeSet(
        Scope(),
        tuple(ChangedFile(path) for path in original["source"]),
        diff=original["diff"],
        configuration=CheckoutConfiguration(),
    )
    result = ModelReviewEvaluator(model).evaluate(
        ReviewInput(changes, read_content=reader("node-postgres-controlled")),
        candidates,
        pass_id="captured",
    )
    assert result.judgments[1].duplicate_of == 0
    assert model.calls == 1


@pytest.mark.parametrize(
    "case,full",
    [
        ("techdebt-final", False),
        ("node-postgres-controlled", False),
        ("node-postgres-controlled", True),
    ],
)
def test_evaluation_batch_uses_captured_source_and_dependency_contracts(case, full):
    captured = json.loads((FIXTURES / (case + ".json")).read_text())
    original = captured["input"]
    candidates = [
        CodeSuggestion.model_validate(item["finding"])
        for item in original["candidates"]
    ]
    indices = (
        tuple(range(len(candidates)))
        if full
        else tuple(item["candidate"] for item in captured["selected"])
    )

    class Model:
        token_limit = 131072
        prompt = ""

        def count_tokens(self, content):
            return len(content) // 4

        def generate_text(self, prompt, *, purpose):
            self.prompt = prompt
            return json.loads(
                (
                    FIXTURES
                    / (case + ("-full-response.json" if full else "-response.json"))
                ).read_text()
            )

    model = Model()
    changes = ChangeSet(
        Scope(),
        tuple(ChangedFile(path) for path in original["source"]),
        title=original["title"],
        description=original["description"],
        diff=original["diff"],
        configuration=CheckoutConfiguration(
            overrides={
                "review.include_documentation_files": True,
                "review.include_imported_files": True,
                # The cases below need a budget tight enough to rank under.
                "review.reading_budget": 15_000,
            }
        ),
    )
    request = ReviewInput(
        changes, read_content=reader(case), source_context=original["source"]
    )
    answer = ModelReviewEvaluator(model)._evaluate(
        request, candidates, pass_id="captured", indices=indices
    )
    payload = json.loads(model.prompt.split("\nReview input:\n", 1)[1])
    assert {judgment.candidate for judgment in answer.judgments} == set(indices)
    assert {item["candidate"] for item in payload["candidates"]} == set(indices)
    if case == "node-postgres-controlled":
        assert "packages/pg/test/suite.js" in payload["source"]
        assert "packages/pg-cursor/index.js" not in payload["source"]
    else:
        assert "src/utils/fileUtils.ts" in payload["source"]


@pytest.mark.parametrize("strategy,present", [("legacy", False), ("direct", True)])
def test_captured_handler_task_retains_changed_parser_dependency(strategy, present):
    captured = json.loads(
        (FIXTURES / "techdebt-discovery-dependencies.json").read_text()
    )
    from src.core.review.contracts import DefaultReviewContextProvider

    changes = ChangeSet(
        Scope(),
        tuple(ChangedFile(path) for path in captured["changed_paths"]),
        configuration=CheckoutConfiguration(
            overrides={
                "review.direct_dependencies_first": strategy == "direct",
                "review.include_documentation_files": True,
                "review.include_imported_files": True,
                # The cases below need a budget tight enough to rank under.
                "review.reading_budget": 15_000,
            }
        ),
    )
    context = DefaultReviewContextProvider().prepare(
        ReviewInput(changes, read_content=captured["files"].get), captured["paths"]
    )
    parser = "src/server/inputParser.ts"
    assert (parser in context.source) is present
    assert sum(map(len, context.source.values())) <= 60000
    if present:
        assert "code.length > MAX_CODE_LENGTH" in context.source[parser]
        assert parser not in context.omitted
    else:
        assert parser in context.omitted


@pytest.mark.parametrize("strategy,present", [("legacy", False), ("direct", True)])
def test_captured_evaluator_prioritizes_dependency_over_generic_search(
    monkeypatch, strategy, present
):
    captured = json.loads(
        (FIXTURES / "node-evaluator-context-priority.json").read_text()
    )
    original = captured["payload"]
    candidates = [
        CodeSuggestion.model_validate(one["finding"]) for one in original["candidates"]
    ]
    candidate_paths = {one.file_name for one in candidates}
    sources = {**original["source"], **captured["required_source"]}

    class Context:
        def prepare(self, request, paths):
            return ReviewSourceContext(source=captured["required_source"])

    class Model:
        token_limit = 1_000_000
        prompt = ""

        def count_tokens(self, text):
            return len(text) // 4

        def generate_text(self, prompt, *, purpose):
            self.prompt = prompt
            return captured["response"]

    monkeypatch.setattr(
        "src.core.review.evaluation.finding_context",
        lambda *args: ReviewSourceContext(
            source={
                path: content
                for path, content in original["source"].items()
                if path not in candidate_paths
            }
        ),
    )
    services = ServiceRegistry().with_service(
        ReviewContextProvider, Context(), "captured"
    )
    changes = ChangeSet(
        Scope(),
        tuple(ChangedFile(path) for path in candidate_paths),
        diff=original["diff"],
        configuration=CheckoutConfiguration(
            overrides={
                "review.direct_dependencies_first": strategy == "direct",
                "review.include_documentation_files": True,
                "review.include_imported_files": True,
                # The cases below need a budget tight enough to rank under.
                "review.reading_budget": 15_000,
            }
        ),
    )
    model = Model()
    ModelReviewEvaluator(model, services).evaluate(
        ReviewInput(changes, read_content=sources.get), candidates, pass_id="captured"
    )
    payload = json.loads(model.prompt.split("\nReview input:\n", 1)[1])
    assert ("packages/pg/lib/query.js" in payload["source"]) is present


def test_repository_constraints_quote_actual_guidance_with_source_lines():
    from src.core.review.contracts import stated_constraints

    captured = json.loads(
        (FIXTURES / "techdebt-discovery-dependencies.json").read_text()
    )
    source = captured["files"]
    rendered = stated_constraints(source)
    assert rendered
    assert "not instructions governing the reviewer" in rendered
    guidance = source["CLAUDE.md"].splitlines()
    selected = [
        (i, text)
        for i, text in enumerate(guidance, 1)
        if "**Path arguments:**" in text or "**Error messages:**" in text
    ]
    assert len(selected) == 2
    for number, text in selected:
        assert f"CLAUDE.md:{number}: {text.strip()}" in rendered
    assert stated_constraints(source, max_chars=1) is None
    assert (
        stated_constraints({"src/server/handlers.ts": source["src/server/handlers.ts"]})
        is None
    )


def test_partial_task_inventory_includes_other_changed_tests_and_reports_truncation():
    from src.core.review.contracts import change_inventory

    captured = json.loads(
        (FIXTURES / "techdebt-discovery-dependencies.json").read_text()
    )
    inventory = change_inventory(captured["changed_paths"], ())
    assert "only a slice" in inventory
    assert all(path in inventory for path in captured["changed_paths"])
    assert "Inventory truncated" not in inventory
    truncated = change_inventory(captured["changed_paths"], (), max_chars=20)
    assert "Inventory truncated" in truncated


@pytest.mark.parametrize("scope", ["repository", "changed-lines"])
@pytest.mark.parametrize("enabled", [True, False])
def test_captured_specialist_gates_do_not_replace_general_policy(scope, enabled):
    from src.core.review.models import scoped_specialist_guidance

    captured = json.loads((FIXTURES / "specialist-policy-conflict.json").read_text())
    checklists = [
        (one["specialty"], one["checklist"]) for one in captured["checklists"]
    ]
    text = scoped_specialist_guidance(
        checklists, finding_scope=scope, include_nitpicks=enabled
    )
    assert "specialist-only publication gates do not set the general" in text
    supplied = json.loads(
        next(line for line in text.splitlines() if line.startswith("["))
    )
    assert supplied == captured["checklists"]
    assert any(
        "Anchor each finding to added or modified code" in one["checklist"]
        for one in supplied
    )
    policy = text.split("## Configured general review policy", 1)[1]
    if scope == "repository":
        assert "Relevant unchanged head code" in policy
    else:
        assert "Anchor findings to changed lines" in policy
    if enabled:
        assert "need not cause a runtime failure" in policy
    else:
        assert "are disabled" in policy


def test_absent_specialist_guidance_adds_no_policy_override():
    from src.core.review.models import scoped_specialist_guidance

    assert (
        scoped_specialist_guidance(
            [], finding_scope="repository", include_nitpicks=True
        )
        is None
    )
