from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from src.api.routes import settings
from src.auth import get_current_user
from src.core.settings.configuration import Configuration
from src.integrations.github.github import GitHub
from src.models.code_review import CodeReviewSummary
from src.models.config import Config


@pytest.mark.parametrize(
    "section,heading",
    [
        ("minor_suggestions", "Minor Suggestions"),
        ("critical_findings", "Critical Issues"),
        ("regressions", "Regressions"),
    ],
)
def test_overview_visibility_is_configurable_through_http(tmp_path, section, heading):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Config.__table__.create(engine)
    app = FastAPI()
    app.include_router(settings.router, prefix="/api/settings")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "42",
        "scope": {"workspace_id": "one"},
    }
    configuration = Configuration(repository="acme/web", workspace="one", user="42")
    summary = CodeReviewSummary(
        overview="Changes request handling.",
        key_improvements=["Handles requests concurrently."],
        regressions=["Requests use more memory."],
        critical_issues=["Reject invalid requests."],
        minor_suggestions=["Name the timeout."],
    )
    key = f"review.overview.show_{section}"
    try:
        with patch("src.config.db.engine", engine), TestClient(app) as client:
            catalogue = client.get("/api/settings/catalogue?scope=workspace")
            assert catalogue.status_code == 200
            offered = next(
                item for item in catalogue.json()["data"] if item["key"] == key
            )
            assert offered["group"] == "Review"
            assert offered["default"] is True
            assert configuration.value(key) is True
            assert heading in GitHub._format_summary(None, summary, configuration)
            response = client.put(
                f"/api/settings/workspace/one/{key}", json={"value": False}
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["value"] is False
            rendered = GitHub._format_summary(None, summary, configuration)
            assert heading not in rendered
            assert "Changes request handling." in rendered
            assert "Key Improvements" in rendered
            assert all(
                other in rendered
                for other in ("Minor Suggestions", "Critical Issues", "Regressions")
                if other != heading
            )
            response = client.delete(f"/api/settings/workspace/one/{key}")
            assert response.status_code == 200, response.text
            assert heading in GitHub._format_summary(None, summary, configuration)
            empty = CodeReviewSummary(
                overview="No findings.", minor_suggestions=[], critical_issues=[]
            )
            assert heading not in GitHub._format_summary(None, empty, configuration)
    finally:
        engine.dispose()


@pytest.mark.parametrize("only_nitpicks", [False, True])
def test_nitpicks_require_opt_in_through_http(tmp_path, only_nitpicks):
    from unittest.mock import Mock

    from src.core.change_context.models import ChangedFile, ChangeSet
    from src.core.scope import Scope
    from src.core.services import ServiceRegistry
    from src.models.code_review import (
        CodeReview,
        CodeSuggestion,
        SuggestionCategory,
        Verdict,
    )
    from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer

    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Config.__table__.create(engine)
    app = FastAPI()
    app.include_router(settings.router, prefix="/api/settings")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "42",
        "scope": {"workspace_id": "one"},
    }
    configuration = Configuration(workspace="one", user="42")
    categories = list(SuggestionCategory)
    important = {
        SuggestionCategory.BUG,
        SuggestionCategory.SECURITY,
        SuggestionCategory.PERFORMANCE,
    }
    if only_nitpicks:
        categories = [category for category in categories if category not in important]
    suggestions = [
        CodeSuggestion(
            file_name="a.py",
            start_line=1,
            end_line=1,
            side="RIGHT",
            suggested_code=None,
            category=category,
            comment=f"Finding for {category.value}",
        )
        for category in categories
    ]
    answer = CodeReview(
        summary=CodeReviewSummary(
            overview="Changes a value.", minor_suggestions=[], critical_issues=[]
        ),
        verdict=Verdict.COMMENT,
        code_suggestions=suggestions,
    )
    changes = ChangeSet(
        scope=Scope.from_mapping({"repository": "acme/web"}),
        configuration=configuration,
        files=(ChangedFile(path="a.py"),),
        diff="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
    )
    reviewer = CodeReviewer(services=ServiceRegistry())
    provider = Mock()
    provider.count_tokens.side_effect = len
    key = "review.include_nitpicks"
    try:
        with (
            patch("src.config.db.engine", engine),
            TestClient(app) as client,
            patch.object(reviewer, "_in_one_pass", return_value=answer) as reading,
        ):
            catalogue = client.get("/api/settings/catalogue?scope=workspace").json()[
                "data"
            ]
            offered = next(item for item in catalogue if item["key"] == key)
            assert offered["group"] == "Review"
            assert offered["default"] is False
            for enabled in (False, True, False):
                if enabled:
                    response = client.put(
                        f"/api/settings/workspace/one/{key}", json={"value": True}
                    )
                    assert response.status_code == 200, response.text
                else:
                    response = client.delete(f"/api/settings/workspace/one/{key}")
                    assert response.status_code == 200, response.text
                result = reviewer.review(changes, provider=provider)
                expected = (
                    suggestions
                    if enabled
                    else [s for s in suggestions if s.category in important]
                )
                assert result.code_suggestions == expected
                assert result.summary.minor_suggestions == [
                    s.comment
                    for s in expected
                    if s.category
                    not in {SuggestionCategory.BUG, SuggestionCategory.SECURITY}
                ]
                assert result.verdict == (
                    Verdict.COMMENT
                    if enabled and only_nitpicks
                    else Verdict.APPROVE if only_nitpicks else Verdict.REQUEST_CHANGES
                )
                knowledge = reading.call_args.args[9].knowledge or ""
                assert ("Nitpicks are disabled" in knowledge) is not enabled
    finally:
        engine.dispose()
