import json
import threading
import time
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session

from src.api.routes import reviews
from src.core.jobs import INTERACTIVE, JobHandler, SQLJobStore, Worker
from src.core.cache import SQLCache
from src.core.jobs.sql import metadata
from src.core.services import ServiceRegistry
from src.events.review_previews import ReviewPreviews
from src.models.cache_entry import CacheEntry
from src.models.cached_review import CachedReview
from src.models.config import Config
from src.models.connected_repository import ConnectedRepository
from src.models.repository import Repository
from src.models.workspace import Workspace

FIXTURES = Path(__file__).parent / "fixtures"
PR = json.loads((FIXTURES / "github-review-pr.json").read_text())
REPOSITORY = json.loads((FIXTURES / "github-review-repository.json").read_text())
RESULT = {"status": "success", "review": {"summary": {"overview": "Review complete."}}}


@pytest.fixture
def preview_api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'reviews.db'}")
    for table in (
        Repository.__table__,
        Workspace.__table__,
        ConnectedRepository.__table__,
        Config.__table__,
        CacheEntry.__table__,
        CachedReview.__table__,
    ):
        table.create(engine)
    metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Repository(
                id=REPOSITORY["id"],
                provider="GitHub",
                name=REPOSITORY["name"],
                full_name=REPOSITORY["full_name"],
                url=REPOSITORY["html_url"],
                private=REPOSITORY["private"],
                archived=REPOSITORY["archived"],
                visibility=REPOSITORY["visibility"],
                owner=REPOSITORY["owner"]["login"],
                owner_type=REPOSITORY["owner"]["type"],
                default_branch=REPOSITORY["default_branch"],
            )
        )
        session.commit()
    monkeypatch.setattr("src.config.db.engine", engine)
    store = SQLJobStore(engine)
    monkeypatch.setattr("src.core.jobs._core", store)
    monkeypatch.setattr("src.core.cache._core", SQLCache())
    monkeypatch.setenv("JWT_SECRET", "your-test-signing-secret-here")
    plugin = type("Plugin", (), {"generate_review": AsyncMock(return_value=RESULT)})()
    from src.core.plugins import plugin_registry

    monkeypatch.setattr(plugin_registry, "get_plugin", lambda name: plugin)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=PR))
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        reviews.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    app = FastAPI()
    app.include_router(reviews.router, prefix="/api/reviews")

    def headers(workspace="one", allowed=True):
        token = jwt.encode(
            {
                "sub": "1",
                "username": "nfebe",
                "github_token": "your-api-key-here",
                "exp": int(time.time()) + 300,
                "scope": {
                    "workspace_id": workspace,
                    "repository_ids": [REPOSITORY["id"]] if allowed else [],
                },
            },
            "your-test-signing-secret-here",
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        yield client, store, plugin, headers
    engine.dispose()


def ask(client, headers, **options):
    response = client.post(
        "/api/reviews/rerun",
        headers=headers(),
        json={
            "repo": REPOSITORY["full_name"],
            "number": PR["number"],
            "background": True,
            **options,
        },
    )
    capture(
        response,
        (
            "queued"
            if response.status_code == 202 and response.json()["data"].get("created")
            else (
                "joined"
                if response.status_code == 202
                else (
                    "cached"
                    if response.json().get("data", {}).get("cached")
                    else "completed"
                )
            )
        ),
    )
    return response


def capture(response, name):
    destination = os.environ.get("REVIEW_CAPTURE_DIR")
    if destination and response.is_success:
        Path(destination, name + ".json").write_text(
            json.dumps(response.json(), indent=2) + "\n"
        )


def test_opening_an_uncached_review_does_not_generate_it(preview_api):
    client, store, plugin, headers = preview_api
    response = client.get(
        "/api/reviews/result",
        headers=headers(),
        params={
            "repo": REPOSITORY["full_name"],
            "number": PR["number"],
            "head_sha": PR["head"]["sha"],
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "missing"
    assert not store.recent()
    plugin.generate_review.assert_not_called()


def test_review_runs_outside_the_http_request_and_can_be_polled(preview_api):
    client, store, plugin, headers = preview_api
    response = ask(client, headers)
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["job_id"]
    plugin.generate_review.assert_not_called()
    services = ServiceRegistry()
    services.contribute(JobHandler, ReviewPreviews(plugin), "test")
    Worker(store, INTERACTIVE, services=services).work(max_jobs=1)
    result = client.get(f"/api/reviews/runs/{job_id}", headers=headers())
    assert result.status_code == 200
    capture(result, "completed")
    assert result.json()["data"]["review"] == RESULT["review"]
    assert plugin.generate_review.call_args.kwargs["revision"] == PR["head"]["sha"]
    cached = ask(client, headers)
    assert cached.status_code == 200
    assert cached.json()["data"]["cached"] is True


def test_legacy_request_still_returns_the_review(preview_api):
    client, store, plugin, headers = preview_api
    response = ask(client, headers, background=False)
    assert response.status_code == 200
    assert response.json()["data"]["review"] == RESULT["review"]
    assert not store.recent()


def test_delivered_background_review_does_not_depend_on_reuse_settings(
    preview_api, monkeypatch
):
    from src.core.cache import cache
    from src.core.jobs import JobRequest, JobOutcome

    client, store, plugin, headers = preview_api
    monkeypatch.setattr(
        "src.utils.review_cache._ttl_seconds", lambda *args, **kwargs: 0
    )
    delivery = store.enqueue(JobRequest(lane="background", kind="review.post"))
    lease = store.claim("background", "delivery-worker", 1)[0]
    cache().set("review.post-result", str(delivery), json.dumps(RESULT), ttl=60)
    store.finish(lease, JobOutcome.ok())
    plugin.generate_review.return_value = {
        "status": "pending",
        "posting_job_id": delivery,
    }
    job_id = ask(client, headers, post=True).json()["data"]["job_id"]
    services = ServiceRegistry()
    services.contribute(JobHandler, ReviewPreviews(plugin), "test")
    Worker(store, INTERACTIVE, services=services).work(max_jobs=1)
    response = client.get(f"/api/reviews/runs/{job_id}", headers=headers())
    assert response.json()["data"]["review"] == RESULT["review"]


def test_stopping_a_review_also_stops_its_waiting_delivery(preview_api):
    from src.core.cache import cache
    from src.core.jobs import JobRequest

    client, store, plugin, headers = preview_api
    job_id = ask(client, headers, post=True).json()["data"]["job_id"]
    delivery = store.enqueue(JobRequest(lane="background", kind="review.post"))
    cache().set("review.preview-delivery", str(job_id), str(delivery), ttl=60)
    response = client.delete(f"/api/reviews/runs/{job_id}", headers=headers())
    assert response.json()["data"]["status"] == "cancelled"
    assert store.read(delivery).state == "cancelled"


def test_status_remains_responsive_while_the_model_is_busy(preview_api):
    client, store, plugin, headers = preview_api
    started, release = threading.Event(), threading.Event()

    async def generate(*args, **kwargs):
        started.set()
        await asyncio.to_thread(release.wait, 10)
        return RESULT

    plugin.generate_review.side_effect = generate
    job_id = ask(client, headers).json()["data"]["job_id"]
    services = ServiceRegistry()
    services.contribute(JobHandler, ReviewPreviews(plugin), "test")
    worker = threading.Thread(
        target=lambda: Worker(store, INTERACTIVE, services=services).work(max_jobs=1)
    )
    worker.start()
    try:
        assert started.wait(5)
        response = client.get(f"/api/reviews/runs/{job_id}", headers=headers())
        assert response.status_code == 200
        capture(response, "running")
        assert response.json()["data"]["status"] == "running"
        assert not release.is_set()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_stop_request_terminates_the_process_doing_the_review(preview_api, tmp_path):
    from src.tests.unit.test_core_jobs_recovery import WORKER
    from src.core.jobs.worker import LOST_EXIT

    client, store, plugin, headers = preview_api
    job_id = ask(client, headers).json()["data"]["job_id"]
    script = WORKER.replace('kind = "test.long"', 'kind = "review.preview"')
    worker = subprocess.Popen(
        [sys.executable, "-c", script, str(store._engine.url)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        until = time.monotonic() + 5
        while store.read(job_id).state != "running" and time.monotonic() < until:
            time.sleep(0.05)
        assert store.read(job_id).state == "running"
        response = client.delete(f"/api/reviews/runs/{job_id}", headers=headers())
        assert response.status_code == 200
        assert worker.wait(timeout=5) == LOST_EXIT
        assert store.read(job_id).state == "cancelled"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)


def test_refresh_coalesces_a_running_review_and_never_queues_a_second_one(preview_api):
    client, store, plugin, headers = preview_api
    first = ask(client, headers).json()["data"]
    lease = store.claim(INTERACTIVE, "worker", 1)[0]
    response = ask(client, headers, refresh=True)
    assert response.status_code == 202
    assert response.json()["data"]["job_id"] == first["job_id"]
    assert response.json()["data"]["created"] is False
    assert store.heartbeat(lease)
    assert len(store.recent()) == 1


def test_cancellation_releases_the_claim_and_cannot_be_undone_by_the_old_worker(
    preview_api,
):
    from src.core.jobs import JobOutcome

    client, store, plugin, headers = preview_api
    job_id = ask(client, headers).json()["data"]["job_id"]
    lease = store.claim(INTERACTIVE, "worker", 1)[0]
    response = client.delete(f"/api/reviews/runs/{job_id}", headers=headers())
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"
    capture(response, "cancelled")
    assert not store.heartbeat(lease)
    store.finish(lease, JobOutcome.ok())
    assert store.read(job_id).state == "cancelled"


def test_review_status_and_cancellation_are_scoped_to_the_workspace(preview_api):
    client, store, plugin, headers = preview_api
    job_id = ask(client, headers).json()["data"]["job_id"]
    for method in (client.get, client.delete):
        assert (
            method(f"/api/reviews/runs/{job_id}", headers=headers("two")).status_code
            == 404
        )
    assert ask(client, lambda: headers(allowed=False)).status_code == 403
    assert store.read(job_id).state == "queued"
