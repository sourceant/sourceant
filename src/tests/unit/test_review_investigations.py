import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.core.change_context import ChangeSet, ChangedFile
from src.core.review.execution import ReviewInput, ReviewParticipant, ParallelReviewPlan
from src.core.review.investigations import (
    ChangedBehaviorInvestigationSource,
    target_source,
)
from src.core.review.snapshot import SnapshotConfiguration
from src.core.scope import Scope
from src.core.settings.definitions import get, SETTINGS
from src.utils.diff_parser import parse_diff
from src.core.review.exclusions import review_diff


def captured_request():
    snapshot = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/review-contracts/node-cited-evidence.json"
        ).read_text()
    )["snapshot"]
    changes = ChangeSet(
        Scope.from_mapping({"repository": snapshot["repository"]}),
        tuple(ChangedFile(one.file_path) for one in parse_diff(snapshot["diff"])),
        diff=snapshot["diff"],
        revision=snapshot["head"],
        base_revision=snapshot["base"],
        configuration=SnapshotConfiguration(
            overrides={
                "review.exclude_patterns": get("review.exclude_patterns").default
            }
        ),
    )
    return ReviewInput(changes, read_content=snapshot["files"].get)


def test_captured_change_is_partitioned_once_with_stable_identities():
    request = captured_request()
    source = ChangedBehaviorInvestigationSource()
    tasks = source.investigations_for(request, 3)
    assert len(tasks) == 3
    diff, _ = review_diff(request.changes.diff, request.changes.configuration)
    expected = {
        one.file_path: tuple(one.hunk_ranges)
        for one in parse_diff(diff)
        if not one.is_binary_file
    }
    assigned = [target for task in tasks for target in task.targets]
    assert len(assigned) == len(expected)
    assert {target.path: target.ranges for target in assigned} == expected
    assert len({task.name for task in tasks}) == len(tasks)
    assert tasks == source.investigations_for(request, 3)


def test_captured_head_excerpts_obey_source_budget_and_report_missing_content():
    request = captured_request()
    task = ChangedBehaviorInvestigationSource().investigations_for(request, 1)[0]
    text, missing = target_source(task, request.read_content, 1200)
    assert len(text) <= 1200
    assert missing
    text, missing = target_source(task, lambda path: None, 1200)
    assert not text
    assert len(missing) == len(task.targets)


def test_investigation_count_cannot_exceed_reading_budget():
    task = ChangedBehaviorInvestigationSource().investigations_for(
        captured_request(), 1
    )[0]
    with pytest.raises(ValueError, match="own task"):
        ParallelReviewPlan(
            (
                ReviewParticipant(
                    "reader", object(), object(), 2, investigations=(task,)
                ),
            )
        )
    with pytest.raises(ValueError, match="between"):
        ChangedBehaviorInvestigationSource().investigations_for(captured_request(), 201)


def test_registered_source_receives_pinned_request_and_cannot_expand_budget():
    from types import SimpleNamespace
    from src.core.services import ServiceRegistry
    from src.core.review.interfaces import ReviewInvestigationSource
    from src.core.review.planning import SettingsReviewPlanSource

    request = captured_request()
    values = {setting.key: setting.default for setting in SETTINGS}
    values.update(
        {
            "review.plan": "premium",
            "review.discovery_strategy": "investigations",
            "review.discovery_passes": 1,
            "review.investigation_limit": 1,
            "review.evaluation_passes": 0,
            "review.minimum_support": 0,
            "review.expert_readings": 10,
        }
    )
    request = replace(
        request,
        changes=replace(
            request.changes, configuration=SnapshotConfiguration(overrides=values)
        ),
    )
    task = ChangedBehaviorInvestigationSource().investigations_for(request, 1)[0]

    class Source:
        tasks = (task, task)

        def investigations_for(self, incoming, limit):
            assert incoming is request
            assert limit == 1
            return self.tasks

    services = ServiceRegistry()
    services.register(ReviewInvestigationSource, Source(), "test")
    with pytest.raises(ValueError, match="exceeded"):
        SettingsReviewPlanSource(services).plan_for(
            request, object(), SimpleNamespace(missing_credentials=lambda: False)
        )
    Source.tasks = (task,)
    plan = SettingsReviewPlanSource(services).plan_for(
        request, object(), SimpleNamespace(missing_credentials=lambda: False)
    )
    assert plan.reviewers[0].expert_repetitions == 0
    assert plan.reviewers[0].repetitions == 1


def test_missing_investigation_evidence_cannot_produce_approval():
    from src.core.analysis import Analysis
    from src.core.review import ParallelReviewOrchestrator, ReviewEvaluation
    from src.models.code_review import CodeReview, Verdict

    request = captured_request()
    task = ChangedBehaviorInvestigationSource().investigations_for(request, 1)[0]

    class Reader:
        def review(self, changes, **options):
            options["coverage"].record(
                "investigation",
                "source evidence",
                answered=False,
                target=task.name,
                reason="Required head source unavailable",
            )
            return CodeReview(verdict=Verdict.APPROVE, code_suggestions=[])

    class Deterministic:
        def evaluate(self, *args, **options):
            return ReviewEvaluation(analysis=Analysis())

    plan = ParallelReviewPlan(
        (ReviewParticipant("reader", Reader(), object(), investigations=(task,)),)
    )
    execution = ParallelReviewOrchestrator(Deterministic()).review(request, plan)
    assert execution.investigation_gaps == ("Required head source unavailable",)
    assert execution.review.verdict == Verdict.COMMENT


def test_dependency_allocation_keeps_captured_implementations_with_their_tests():
    root = Path(__file__).parents[1] / "fixtures/review-contracts"
    captured = json.loads((root / "techdebt-discovery-dependencies.json").read_text())
    diff = json.loads((root / "techdebt-final.json").read_text())["input"]["diff"]
    request = ReviewInput(
        ChangeSet(
            Scope(),
            tuple(ChangedFile(path) for path in captured["changed_paths"]),
            diff=diff,
            configuration=SnapshotConfiguration(
                overrides={"review.investigation_allocation": "dependencies"}
            ),
        ),
        read_content=captured["files"].get,
    )
    source = ChangedBehaviorInvestigationSource()
    tasks = source.investigations_for(request, 3)
    groups = [{target.path for target in task.targets} for task in tasks]
    assert len(groups) == 3
    assert sum(map(len, groups)) == len(set.union(*groups))
    assert set.union(*groups) == set(captured["changed_paths"])
    for implementation, test in [
        (
            "src/core/customRulesEngine.ts",
            "src/core/__tests__/customRulesEngine.test.ts",
        ),
        ("src/server/handlers.ts", "src/server/__tests__/handlers.test.ts"),
        ("src/server/inputParser.ts", "src/server/__tests__/inputParser.test.ts"),
    ]:
        assert any({implementation, test} <= group for group in groups), groups
    assert any(
        {"src/server/handlers.ts", "src/server/inputParser.ts"} <= group
        for group in groups
    )
    assert tasks == source.investigations_for(request, 3)
