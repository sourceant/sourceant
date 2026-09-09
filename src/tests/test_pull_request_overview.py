import json
import jwt
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.routes import reviews
from src.core.plugins.plugin_registry import plugin_registry
from src.models.code_review import (
    CodeReview,
    CodeReviewSummary,
    CodeSuggestion,
    SuggestionCategory,
    Side,
    Verdict,
)
from src.integrations.github.github import GitHub
from src.plugins.builtin.code_reviewer import plugin as reviewer_plugin
from src.plugins.builtin.code_reviewer.overview import summarize_changes
from src.tests.test_dashboard_backend_api import api as api

FIXTURES = Path(__file__).parent / "fixtures/review-overview"


@pytest.mark.parametrize("budget", [100000, 1])
@pytest.mark.parametrize("with_findings", [False, True])
def test_http_preview_overview_reads_all_pr_changes(
    api, monkeypatch, budget, with_findings
):
    client, headers, _, _ = api
    client.app.include_router(reviews.router, prefix="/api/reviews")
    monkeypatch.setattr(
        "src.api.routes.requirements.connected_names",
        lambda user: ["sourceant/sourceant"],
    )
    monkeypatch.setattr(reviews, "save_cached_review", lambda *args, **kwargs: None)
    metadata = json.loads((FIXTURES / "pull.json").read_text())
    monkeypatch.setattr(
        httpx.AsyncClient,
        "get",
        AsyncMock(return_value=httpx.Response(200, json=metadata)),
    )
    monkeypatch.setattr(
        plugin_registry, "get_plugin", lambda name: reviewer_plugin.CodeReviewerPlugin()
    )
    github = MagicMock()
    github.get_diff.return_value = (FIXTURES / "full.diff").read_text()
    github.get_existing_bot_review_comments.return_value = []
    github.get_previous_review_summary.return_value = "Only changes priority."
    monkeypatch.setattr(reviewer_plugin, "GitHub", lambda: github)
    provider = MagicMock()
    provider.count_tokens.side_effect = len
    findings = (
        [
            CodeSuggestion(
                file_name="src/core/topology/suggestions.py",
                start_line=line,
                end_line=line,
                side=Side.RIGHT,
                category=category,
                comment=comment,
                existing_code=existing,
                suggested_code=replacement,
            )
            for line, category, comment, existing, replacement in (
                (
                    6,
                    SuggestionCategory.BUG,
                    "This fails for iterators consumed by the first traversal. Materialize repositories before traversing them again.",
                    "    parents = {name: name for name in repositories}",
                    "    repositories = tuple(repositories)\n    parents = {name: name for name in repositories}",
                ),
                (
                    20,
                    SuggestionCategory.CLARITY,
                    "The accumulator name is unclear. Rename it to describe the repository groups.",
                    "    groups = {}",
                    "    repository_groups = {}",
                ),
            )
        ]
        if with_findings
        else []
    )
    provider.generate_code_review.return_value = CodeReview(
        verdict=Verdict.COMMENT,
        code_suggestions=findings,
        summary=CodeReviewSummary(
            overview="Only changes priority.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        ),
    )
    provider.generate_summary.return_value = CodeReviewSummary(
        overview="Adds system suggestions and requirement priority.",
        key_improvements=["Groups related repositories."],
        minor_suggestions=[],
        critical_issues=[],
    )
    monkeypatch.setattr(reviewer_plugin, "provider_for", lambda **kwargs: provider)
    monkeypatch.setattr(
        "src.plugins.builtin.code_reviewer.reviewing.CodeReviewer._budget",
        staticmethod(lambda repository: budget),
    )
    credentials = headers()
    claims = jwt.decode(
        credentials["Authorization"][7:],
        "dashboard-backend-test-secret",
        algorithms=["HS256"],
    )
    claims["github_token"] = "test-placeholder"
    credentials["Authorization"] = "Bearer " + jwt.encode(
        claims, "dashboard-backend-test-secret", algorithm="HS256"
    )
    response = client.post(
        "/api/reviews/rerun",
        headers=credentials,
        json={
            "repo": "sourceant/sourceant",
            "number": metadata["number"],
            "refresh": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "success"
    assert (
        response.json()["data"]["review"]["summary"]["overview"]
        == "Adds system suggestions and requirement priority."
    )
    supplied = [
        json.loads(call.kwargs["change_context"])
        for call in provider.generate_summary.call_args_list
        if "change_context" in call.kwargs
    ]
    diffs = "\n".join(item.get("diff", "") for item in supplied)
    assert "def suggest_groups" in diffs
    assert 'sa.Column("priority"' in diffs
    summary = CodeReviewSummary.model_validate(
        response.json()["data"]["review"]["summary"]
    )
    assert summary.critical_issues == [
        finding.comment
        for finding in findings
        if finding.category == SuggestionCategory.BUG
    ]
    assert summary.minor_suggestions == [
        finding.comment
        for finding in findings
        if finding.category == SuggestionCategory.CLARITY
    ]
    rendered = GitHub._format_summary(None, summary)
    assert ("### 💡 Minor Suggestions" in rendered) == with_findings
    assert ("### 🚨 Critical Issues" in rendered) == with_findings
    assert provider.generate_summary.call_count == (3 if budget == 1 else 1)
    provider.generate_text.assert_not_called()
    if budget == 1:
        assert len([item for item in supplied if "diff" in item]) == 2
        assert len(supplied[-1]["parts_of_the_same_pull_request"]) == 2
    github.post_review.assert_not_called()


def test_empty_overview_cannot_replace_the_standing_overview():
    provider = MagicMock()
    provider.count_tokens.return_value = 1
    provider.generate_summary.return_value = CodeReviewSummary(
        overview=" ", key_improvements=[], minor_suggestions=[], critical_issues=[]
    )
    with pytest.raises(ValueError, match="did not produce"):
        summarize_changes(
            (FIXTURES / "full.diff").read_text(), provider, "sourceant/sourceant"
        )
