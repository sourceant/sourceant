import json
import jwt
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.routes import reviews
from src.core.plugins.plugin_registry import plugin_registry
from src.models.code_review import CodeReview, CodeReviewSummary, Verdict
from src.plugins.builtin.code_reviewer import plugin as reviewer_plugin
from src.plugins.builtin.code_reviewer.overview import pull_request_overview
from src.tests.test_dashboard_backend_api import api as api

FIXTURES = Path(__file__).parent / "fixtures/review-overview"


@pytest.mark.parametrize("budget", [100000, 1])
def test_http_preview_overview_reads_all_pr_changes(api, monkeypatch, budget):
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
    provider.generate_code_review.return_value = CodeReview(
        verdict=Verdict.COMMENT,
        code_suggestions=[],
        summary=CodeReviewSummary(
            overview="Only changes priority.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        ),
    )
    provider.generate_summary.return_value = (
        provider.generate_code_review.return_value.summary
    )
    provider.generate_text.return_value = (
        "Adds system suggestions and requirement priority."
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
        json.loads(call.args[0].split("\n\n", 1)[1])
        for call in provider.generate_text.call_args_list
    ]
    diffs = "\n".join(item.get("diff", "") for item in supplied)
    assert "def suggest_groups" in diffs
    assert 'sa.Column("priority"' in diffs
    assert all(
        call.kwargs["purpose"] == "summary"
        for call in provider.generate_text.call_args_list
    )
    if budget == 1:
        assert len([item for item in supplied if "diff" in item]) == 2
        assert len(supplied[-1]["parts_of_the_same_pull_request"]) == 2
    github.post_review.assert_not_called()


def test_empty_overview_cannot_replace_the_standing_overview():
    provider = MagicMock()
    provider.count_tokens.return_value = 1
    provider.generate_text.return_value = " "
    with pytest.raises(ValueError, match="did not produce"):
        pull_request_overview(
            (FIXTURES / "full.diff").read_text(), provider, "sourceant/sourceant"
        )
