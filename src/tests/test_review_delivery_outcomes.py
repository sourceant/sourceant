import asyncio
import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from litellm import ModelResponse
from litellm.exceptions import BadRequestError
from sqlalchemy import select
from sqlmodel import create_engine

from src.api.routes import pr, settings
from src.auth import get_current_user
from src.core import jobs
from src.core.analysis import Analysis
from src.core.jobs import INTERACTIVE, JobHandler, SQLJobStore, Worker
from src.core.jobs.sql import job_table
from src.core.plugins import event_hooks
from src.core.services import ServiceRegistry
from src.events.delivery import Deliveries
from src.events.review_posting import ReviewPosting
from src.models.config import Config
from src.models.repository_event import RepositoryEvent
from src.models.review_record import ReviewRecord
from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "failure",
    ["generation", "posting", "none", "fallback", "summary_retry", "summary_invalid"],
)
def test_webhook_job_reflects_review_and_posting_outcomes(
    monkeypatch, tmp_path, failure
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'review.db'}",
        connect_args={"check_same_thread": False},
    )
    for model in (Config, RepositoryEvent, ReviewRecord):
        model.__table__.create(engine)
    store = SQLJobStore(engine, create_schema=True)
    monkeypatch.setattr("src.config.db.engine", engine)
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    monkeypatch.setattr(jobs, "_core", store)
    monkeypatch.setattr(
        "src.controllers.repository_event_controller.STATELESS_MODE", False
    )
    monkeypatch.setattr("src.utils.review_record_service.STATELESS_MODE", False)
    monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "database")
    monkeypatch.setattr("src.config.settings.QUEUE_MODE", "database")
    monkeypatch.setattr(
        "src.events.delivery.tenant_for", lambda repo: ("6", "workspace")
    )
    monkeypatch.setattr(event_hooks, "_event_subscribers", {})
    monkeypatch.setattr(pr, "GITHUB_SECRET", "")
    monkeypatch.setattr(
        "src.core.usage.record_completion", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "src.llms.litellm_provider.LiteLLMProvider.count_tokens",
        lambda self, text: len(text),
    )
    services = ServiceRegistry()
    plugin = CodeReviewerPlugin()
    plugin.services = services
    asyncio.run(plugin._initialize())

    github = Mock()
    github.get_diff.return_value = (FIXTURES / "review-overview/full.diff").read_text()
    github.get_existing_bot_review_comments.return_value = []
    github.get_previous_review_summary.return_value = None
    github.get_file_content.return_value = None
    github.has_existing_bot_approval.return_value = False
    github.post_review.return_value = {
        "status": (
            "error"
            if failure == "posting"
            else ("partial_success" if failure == "fallback" else "success")
        ),
        "message": "Review posting failed" if failure == "posting" else "Review posted",
    }
    monkeypatch.setattr(
        "src.plugins.builtin.code_reviewer.plugin.GitHub", lambda: github
    )
    monkeypatch.setattr("src.events.review_posting.GitHub", lambda: github)
    monkeypatch.setattr(
        "src.plugins.builtin.code_reviewer.plugin.written_out",
        lambda *args: nullcontext((tmp_path, ())),
    )
    monkeypatch.setattr(
        "src.plugins.builtin.code_reviewer.plugin.examine", lambda *args: Analysis()
    )

    captures = {
        name: ModelResponse(
            **json.loads((FIXTURES / f"deepseek/{name}.json").read_text())
        )
        for name in ("review", "overview")
    }

    summaries = 0

    def answer(**kwargs):
        nonlocal summaries
        if failure == "generation":
            raise BadRequestError(
                message=(FIXTURES / "deepseek/unsupported-format.json").read_text(),
                model="deepseek-v4-flash",
                llm_provider="deepseek",
            )
        assert kwargs["response_format"] == {"type": "json_object"}
        instruction = kwargs["messages"][-1]["content"]
        if '"CodeReviewOverview"' in instruction:
            summaries += 1
            if failure == "summary_invalid" or (
                failure == "summary_retry" and summaries == 1
            ):
                return captures["review"]
        return captures[
            "review" if '"CodeReviewFindings"' in instruction else "overview"
        ]

    completion = Mock(side_effect=answer)
    monkeypatch.setattr("litellm.completion", completion)
    app = FastAPI()
    app.include_router(pr.router, prefix="/api/prs")
    app.include_router(settings.router, prefix="/api/settings")
    app.dependency_overrides[pr.get_gateway_scope] = lambda: {
        "workspace_id": "6",
        "owner_id": "1",
    }
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "1",
        "scope": {"workspace_id": "6"},
    }
    with TestClient(app) as client:
        for key, value in (
            ("model.name", "deepseek/deepseek-v4-flash"),
            ("model.api_key", "your-api-key-here"),
        ):
            result = client.put(f"/api/settings/user/1/{key}", json={"value": value})
            assert result.status_code == 200, result.text
        payload = json.loads((FIXTURES / "deepseek/webhook.json").read_text())
        payload["pull_request"].update(
            json.loads((FIXTURES / "review-overview/pull.json").read_text())
        )
        result = client.post(
            "/api/prs/github-webhook",
            json=payload,
            headers={"X-GitHub-Event": "pull_request"},
        )
        assert result.status_code == 201, result.text
        services.contribute(JobHandler, Deliveries(services), "sourceant_core")
        services.contribute(JobHandler, ReviewPosting(), "sourceant_core")
        worker = Worker(store, INTERACTIVE, services=services)
        assert worker.work(max_jobs=1, max_time=15) == 1
        with engine.connect() as connection:
            queued = connection.execute(
                select(job_table.c.id).where(job_table.c.kind == "review.post")
            ).first()
        if queued:
            assert worker.work(max_jobs=1, max_time=15) == 1
    with engine.connect() as connection:
        job = (
            connection.execute(select(job_table).order_by(job_table.c.id.desc()))
            .mappings()
            .first()
        )
        records = connection.execute(select(ReviewRecord)).all()
    engine.dispose()
    if failure in ("generation", "posting", "summary_invalid"):
        assert job["state"] == ("failed" if failure == "posting" else "dead"), job
        expected = (
            "response_format" if failure == "generation" else "Review posting failed"
        )
        if failure == "summary_invalid":
            expected = "Invalid summary output after one retry"
        assert expected in job["error"]
        assert not records
    else:
        assert job["state"] == "succeeded", job["error"]
        assert len(records) == 1
    if failure in ("generation", "summary_invalid"):
        github.post_review.assert_not_called()
        if failure == "generation":
            assert completion.call_count <= 2
    else:
        github.post_review.assert_called_once()

    if failure in ("summary_retry", "summary_invalid"):
        assert summaries == 2
        assert completion.call_count == 3
