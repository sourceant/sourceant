from threading import Barrier

import pytest

from src.core.analysis import Analysis, Analyzer, AnalyzerFinding
from src.core.change_context import ChangeSet, ChangedFile
from src.core.review import (
    DeterministicReviewEvaluator,
    EvaluationParticipant,
    EvaluationStatus,
    FindingEvaluation,
    ParallelReviewPlan,
    ParallelReviewOrchestrator,
    ReviewEvaluation,
    ReviewInput,
    ReviewParticipant,
    ReviewPlan,
    ReviewOrchestrator,
    ModelReviewEvaluator,
)
from src.core.scope import Scope
from src.core.services import ServiceRegistry
from src.models.code_review import (
    CodeReview,
    CodeSuggestion,
    Side,
    SuggestionCategory,
    Verdict,
)


def request(analysis=None):
    return ReviewInput(
        ChangeSet(
            Scope(),
            (ChangedFile("app.py"),),
            diff="diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
        ),
        analysis=analysis,
    )


def finding():
    return CodeSuggestion(
        file_name="app.py",
        start_line=1,
        end_line=1,
        side=Side.RIGHT,
        comment="The changed behavior fails.",
        category=SuggestionCategory.BUG,
        suggested_code="",
    )


class Reader:
    def __init__(self, barrier=None, broken=False):
        self.barrier = barrier
        self.broken = broken
        self.identities = []

    def review(self, changes, *, provider, told, **options):
        self.identities.append((provider, told[-1].body))
        if self.barrier:
            self.barrier.wait(timeout=3)
        if self.broken:
            raise RuntimeError("unavailable")
        return CodeReview(verdict=Verdict.COMMENT, code_suggestions=[finding()])


class Evaluator:
    def __init__(self, status, barrier=None):
        self.status = status
        self.barrier = barrier

    def evaluate(self, request, candidates, *, pass_id):
        assert len(candidates) == 1
        if self.barrier:
            self.barrier.wait(timeout=3)
        return ReviewEvaluation(
            (
                FindingEvaluation(
                    0,
                    self.status,
                    "Source establishes the result",
                    ("app.py:1",),
                ),
            )
        )


def test_reviews_and_deterministic_evaluation_run_concurrently():
    barrier = Barrier(3)

    class Deterministic:
        def evaluate(self, request, candidates, *, pass_id):
            barrier.wait(timeout=3)
            return ReviewEvaluation(analysis=Analysis(ran=("lint",)))

    reader = Reader(barrier)
    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", reader, object(), 2),), concurrency=3
    )
    answer = ParallelReviewOrchestrator(Deterministic()).review(request(), plan)
    assert answer.review is not None
    assert all(not one.error for one in answer.reviews)
    assert not answer.evaluations[0].error
    assert answer.producers == (("reader:1", "reader:2"),)
    assert reader.identities[0][1] != reader.identities[1][1]
    assert isinstance(plan, ReviewPlan)
    assert isinstance(ParallelReviewOrchestrator(), ReviewOrchestrator)


def test_different_models_and_evaluation_repetitions_run_in_parallel():
    first, second = object(), object()
    reader = Reader()
    evaluator = Evaluator(EvaluationStatus.SUPPORTED, Barrier(2))
    plan = ParallelReviewPlan(
        (
            ReviewParticipant("first", reader, first),
            ReviewParticipant("second", reader, second),
        ),
        (EvaluationParticipant("check", evaluator, 2),),
        minimum_support=2,
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert len(answer.review.code_suggestions) == 1
    assert {one[0] for one in reader.identities} == {first, second}
    assert all(not one.error for one in answer.evaluations)


def test_rejection_threshold_preserves_candidate_and_its_evidence():
    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", Reader(), object()),),
        (EvaluationParticipant("challenge", Evaluator(EvaluationStatus.REJECTED)),),
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert answer.review.code_suggestions == []
    assert len(answer.candidates) == 1
    assert answer.evaluations[1].result.judgments[0].evidence == ("app.py:1",)


def test_failed_review_is_distinct_from_clean_review():
    plan = ParallelReviewPlan(
        (ReviewParticipant("broken", Reader(broken=True), object()),)
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert answer.review is None
    assert answer.reviews[0].error == "RuntimeError"


def test_evaluated_duplicates_merge_across_producers_but_distinct_issues_survive():
    class Multiple:
        def review(self, *args, **kwargs):
            return CodeReview(
                verdict=Verdict.COMMENT,
                code_suggestions=[
                    finding(),
                    finding().model_copy(
                        update={"comment": "The same failure needs the same guard."}
                    ),
                    finding().model_copy(
                        update={"comment": "A separate failure leaks a resource."}
                    ),
                ],
            )

    class Challenge:
        def evaluate(self, request, candidates, *, pass_id):
            assert len(candidates) == 4
            return ReviewEvaluation(
                tuple(
                    FindingEvaluation(
                        index,
                        EvaluationStatus.SUPPORTED,
                        "Source establishes the cause and fix",
                        ("app.py:1",),
                        duplicate_of=(0 if index in {1, 3} else None),
                    )
                    for index in range(4)
                )
            )

    analysis = Analysis(
        findings=(
            AnalyzerFinding(
                path="app.py",
                start_line=1,
                end_line=1,
                rule="guard",
                message="The changed behavior fails.",
                severity="error",
                tool="lint",
            ),
        )
    )
    answer = ParallelReviewOrchestrator().review(
        request(analysis),
        ParallelReviewPlan(
            (ReviewParticipant("reader", Multiple(), object()),),
            (EvaluationParticipant("challenge", Challenge()),),
            minimum_support=1,
        ),
    )
    assert len(answer.review.code_suggestions) == 2
    assert answer.analysis.findings == ()
    assert answer.finding_groups == ((0, 1, 3), (2,))
    assert len(answer.candidates) == 4
    assert answer.review.verdict == Verdict.REQUEST_CHANGES


@pytest.mark.parametrize("target", [-1, 1, 2, True])
def test_invalid_duplicate_links_fail_evaluation(target):
    class Multiple:
        def review(self, *args, **kwargs):
            return CodeReview(
                verdict=Verdict.COMMENT,
                code_suggestions=[
                    finding(),
                    finding().model_copy(update={"comment": "Another claim."}),
                ],
            )

    class Challenge:
        def evaluate(self, request, candidates, *, pass_id):
            return ReviewEvaluation(
                (
                    FindingEvaluation(
                        0, EvaluationStatus.SUPPORTED, "Source", ("app.py:1",)
                    ),
                    FindingEvaluation(
                        1,
                        EvaluationStatus.SUPPORTED,
                        "Source",
                        ("app.py:1",),
                        duplicate_of=target,
                    ),
                )
            )

    answer = ParallelReviewOrchestrator().review(
        request(Analysis()),
        ParallelReviewPlan(
            (ReviewParticipant("reader", Multiple(), object()),),
            (EvaluationParticipant("challenge", Challenge()),),
            minimum_support=1,
        ),
    )
    assert answer.evaluations[1].error == "ValueError"
    assert answer.review.code_suggestions == []


def test_disagreement_about_duplicate_identity_preserves_both_findings():
    class Multiple:
        def review(self, *args, **kwargs):
            return CodeReview(
                verdict=Verdict.COMMENT,
                code_suggestions=[
                    finding(),
                    finding().model_copy(update={"comment": "Another claim."}),
                ],
            )

    class Challenge:
        def __init__(self, target):
            self.target = target

        def evaluate(self, request, candidates, *, pass_id):
            return ReviewEvaluation(
                (
                    FindingEvaluation(
                        0, EvaluationStatus.SUPPORTED, "Source", ("app.py:1",)
                    ),
                    FindingEvaluation(
                        1,
                        EvaluationStatus.SUPPORTED,
                        "Source",
                        ("app.py:1",),
                        duplicate_of=self.target,
                    ),
                )
            )

    answer = ParallelReviewOrchestrator().review(
        request(Analysis()),
        ParallelReviewPlan(
            (ReviewParticipant("reader", Multiple(), object()),),
            (
                EvaluationParticipant("first", Challenge(0)),
                EvaluationParticipant("second", Challenge(None)),
            ),
            minimum_support=2,
        ),
    )
    assert len(answer.review.code_suggestions) == 2
    assert answer.finding_groups == ((0,), (1,))


def test_rejected_candidate_within_tolerance_does_not_degrade_coverage():
    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", Reader(), object()),),
        (EvaluationParticipant("challenge", Evaluator(EvaluationStatus.REJECTED)),),
        maximum_rejections=1,
        minimum_support=1,
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert answer.review.code_suggestions == []
    assert answer.review.verdict == Verdict.APPROVE
    assert answer.review.summary.minor_suggestions == []


def test_unresolved_candidate_within_tolerance_degrades_coverage():
    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", Reader(), object()),),
        (EvaluationParticipant("challenge", Evaluator(EvaluationStatus.UNRESOLVED)),),
        maximum_rejections=1,
        minimum_support=1,
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert answer.review.code_suggestions == []
    assert answer.review.verdict == Verdict.COMMENT
    assert any("incomplete" in one for one in answer.review.summary.minor_suggestions)


def test_evaluator_failure_is_not_support():
    class Broken:
        def evaluate(self, *args, **kwargs):
            raise RuntimeError("unavailable")

    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", Reader(), object()),),
        (EvaluationParticipant("broken", Broken()),),
        minimum_support=1,
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert answer.review.code_suggestions == []
    assert answer.evaluations[1].error == "RuntimeError"
    assert answer.review.verdict == Verdict.COMMENT


def test_invalid_evaluator_votes_cannot_accept_candidates():
    class Invalid:
        def evaluate(self, *args, **kwargs):
            return ReviewEvaluation(
                (FindingEvaluation(0, EvaluationStatus.SUPPORTED, "unsupported", ()),)
            )

    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", Reader(), object()),),
        (EvaluationParticipant("invalid", Invalid()),),
        minimum_support=1,
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert answer.evaluations[1].error == "ValueError"
    assert answer.review.code_suggestions == []


def test_deterministic_evaluator_reuses_evidence_without_endorsing_model_claims():
    analysis = Analysis(
        (AnalyzerFinding("app.py", 1, 1, "lint", "Lint error"),), ran=("lint",)
    )
    answer = DeterministicReviewEvaluator().evaluate(
        request(analysis), [finding()], pass_id="one"
    )
    assert answer.analysis == analysis
    assert answer.judgments == ()


def test_missing_checkout_is_reported():
    answer = DeterministicReviewEvaluator().evaluate(request(), (), pass_id="one")
    assert answer.analysis.unavailable == ("checkout",)


def test_deterministic_error_prevents_approval():
    class Clean:
        def review(self, *args, **kwargs):
            return CodeReview(verdict=Verdict.APPROVE, code_suggestions=[])

    analysis = Analysis(
        (
            AnalyzerFinding(
                "app.py",
                1,
                1,
                "syntax",
                "Syntax error",
                severity="error",
            ),
        ),
        ran=("lint",),
    )
    answer = ParallelReviewOrchestrator().review(
        request(analysis),
        ParallelReviewPlan((ReviewParticipant("clean", Clean(), object()),)),
    )
    assert answer.review.verdict == Verdict.REQUEST_CHANGES
    assert "Syntax error" in answer.review.summary.critical_issues[0]


def test_model_evaluator_challenges_candidates_with_distinct_passes():
    import json

    class Model:
        def __init__(self):
            self.prompts = []

        def generate_text(self, prompt, *, purpose):
            self.prompts.append(prompt)
            assert purpose == "review-evaluation"
            return json.dumps(
                {
                    "judgments": [
                        {
                            "candidate": 0,
                            "status": "unresolved",
                            "reason": "Missing context",
                            "evidence": [],
                        }
                    ]
                }
            )

    from dataclasses import replace

    supplied = request()
    supplied = replace(
        supplied,
        changes=replace(
            supplied.changes,
            title="Return the request to its caller",
            description="The caller installs error handlers on the returned request.",
        ),
    )
    model = Model()
    evaluator = ModelReviewEvaluator(model)
    for identity in ("first", "second"):
        answer = evaluator.evaluate(supplied, [finding()], pass_id=identity)
        assert answer.judgments[0].status == EvaluationStatus.UNRESOLVED
    assert model.prompts[0] != model.prompts[1]
    context = json.loads(model.prompts[0].split("\nReview input:\n", 1)[1])
    assert context["title"] == supplied.changes.title
    assert context["description"] == supplied.changes.description


def test_model_evaluator_bounds_related_source_and_reports_omissions():
    import json
    from dataclasses import replace

    class Model:
        token_limit = 131072
        prompt = ""

        def count_tokens(self, value):
            return len(value)

        def generate_text(self, prompt, *, purpose):
            self.prompt = prompt
            return json.dumps(
                {
                    "judgments": [
                        {
                            "candidate": 0,
                            "status": "unresolved",
                            "reason": "Missing additional context",
                            "evidence": [],
                        }
                    ]
                }
            )

    model = Model()
    supplied = replace(
        request(),
        source_context={
            "caller.py": "use(request.query)\n",
            "oversized.py": "x" * 30000,
        },
    )
    ModelReviewEvaluator(model).evaluate(supplied, [finding()], pass_id="one")
    context = json.loads(model.prompt.split("\nReview input:\n", 1)[1])
    assert context["source"]["caller.py"] == supplied.source_context["caller.py"]
    assert "oversized.py" not in context["source"]
    assert context["omitted_context"] == ["oversized.py"]


def test_model_evaluator_rejects_missing_votes():
    class Model:
        def generate_text(self, *args, **kwargs):
            return '{"judgments": []}'

    with pytest.raises(ValueError):
        ModelReviewEvaluator(Model()).evaluate(request(), [finding()], pass_id="one")


def test_unverified_guard_bypass_cannot_support_a_finding():
    class Model:
        def generate_text(self, prompt, *, purpose):
            return '{"judgments": [{"candidate": 0, "status": "supported", "reason": "A library may bypass the handler", "evidence": ["response.once(error, reject)"], "unverified_assumptions": ["The library bypasses the registered error handler"]}]}'

    result = ModelReviewEvaluator(Model()).evaluate(
        request(), [finding()], pass_id="one"
    )
    assert result.judgments[0].status == EvaluationStatus.UNRESOLVED
    assert result.judgments[0].duplicate_of is None
    assert "Unverified assumptions" in result.judgments[0].reason


@pytest.mark.parametrize("enabled", [False, True])
def test_model_evaluator_respects_configured_quality_policy(monkeypatch, enabled):
    from src.core.settings.configuration import Configuration

    monkeypatch.setattr(Configuration, "value", lambda self, key: enabled)

    class Model:
        def generate_text(self, prompt, *, purpose):
            if enabled:
                assert "these concerns need not demonstrate a runtime failure" in prompt
                assert "Omit optional type refactors" not in prompt
            else:
                assert "Omit optional type refactors" in prompt
                assert (
                    "these concerns need not demonstrate a runtime failure"
                    not in prompt
                )
            assert "fixing one leaves the other unfixed" in prompt
            return '{"judgments": [{"candidate": 0, "status": "unresolved", "reason": "Missing context", "evidence": []}]}'

    ModelReviewEvaluator(Model()).evaluate(request(), [finding()], pass_id="one")


def test_deterministic_analyzers_run_in_parallel_and_filter_unchanged_lines(tmp_path):
    barrier = Barrier(2)

    class Linter:
        name = "lint"

        def available(self):
            return True

        def examine(self, root, paths):
            barrier.wait(timeout=3)
            return (
                AnalyzerFinding("app.py", 1, 1, "lint", "Changed error"),
                AnalyzerFinding("app.py", 10, 10, "lint", "Existing error"),
            )

    services = ServiceRegistry()
    services.contribute(Analyzer, Linter(), "one")
    services.contribute(Analyzer, Linter(), "two")
    from dataclasses import replace

    answer = DeterministicReviewEvaluator(services).evaluate(
        replace(request(), root=tmp_path),
        (),
        pass_id="one",
    )
    assert len(answer.analysis.findings) == 2
    assert all(one.start_line == 1 for one in answer.analysis.findings)
    assert answer.analysis.unavailable == ()


def test_analyzer_reports_unsupported_files_without_claiming_clean(tmp_path):
    from dataclasses import replace

    class OtherLanguage:
        name = "other-language"
        languages = ("typescript",)
        checks = ("lint",)

        def supports(self, path):
            return path.endswith(".ts")

        def available(self):
            return True

        def examine(self, *args):
            raise AssertionError("Unsupported files must not be examined")

    services = ServiceRegistry()
    services.contribute(Analyzer, OtherLanguage(), "lint")
    answer = DeterministicReviewEvaluator(services).evaluate(
        replace(request(), root=tmp_path),
        (),
        pass_id="one",
    )
    assert answer.analysis.ran == ()
    assert answer.analysis.coverage[0].unsupported == ("app.py",)
    assert answer.analysis.coverage[0].languages == ("typescript",)


@pytest.mark.parametrize(
    "options", [{"concurrency": 0}, {"minimum_support": -1}, {"maximum_rejections": -1}]
)
def test_invalid_plan_is_refused(options):
    with pytest.raises(ValueError):
        ParallelReviewPlan(
            (ReviewParticipant("reader", Reader(), object()),), **options
        )


def test_repository_scope_retains_static_findings_outside_changed_lines(monkeypatch):
    from src.core.settings.configuration import Configuration

    original = Configuration.value
    monkeypatch.setattr(
        Configuration,
        "value",
        lambda self, key: (
            "repository" if key == "review.finding_scope" else original(self, key)
        ),
    )
    observed = AnalyzerFinding("app.py", 10, 10, "lint", "Existing error")
    answer = DeterministicReviewEvaluator().evaluate(
        request(Analysis(findings=(observed,))), (), pass_id="one"
    )
    assert answer.analysis.findings == (observed,)


def test_captured_reversed_range_is_retained_as_unresolved_execution_detail():
    import json
    from pathlib import Path

    sample = Path(__file__).parents[1] / "fixtures/deepseek/reversed-finding-range.json"
    candidate = CodeSuggestion.model_validate(json.loads(sample.read_text()))

    class CapturedReader:
        def review(self, changes, **options):
            return CodeReview(verdict=Verdict.COMMENT, code_suggestions=[candidate])

    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", CapturedReader(), object()),),
        (EvaluationParticipant("check", Evaluator(EvaluationStatus.SUPPORTED)),),
    )
    answer = ParallelReviewOrchestrator().review(request(Analysis()), plan)
    assert answer.candidates == (candidate,)
    assert not answer.review.code_suggestions
    assert answer.review.verdict == Verdict.COMMENT
    assert any("incomplete" in note for note in answer.review.summary.minor_suggestions)
