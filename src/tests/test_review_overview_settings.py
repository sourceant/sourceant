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
