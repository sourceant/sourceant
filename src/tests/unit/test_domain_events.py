import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from src.core.domain_events import DomainEventDelivery, emit
from src.core.jobs.interfaces import JobHandler
from src.core.jobs.memory import InMemoryJobStore
from src.core.jobs.models import BACKGROUND
from src.core.jobs.worker import Worker
from src.core.services import ServiceRegistry


def test_event_survives_queueing_and_is_signed_when_worker_delivers(monkeypatch):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            timestamp = self.headers["X-Sourceant-Timestamp"]
            expected = hmac.new(
                b"test-secret", timestamp.encode() + b"." + body, hashlib.sha256
            ).hexdigest()
            assert hmac.compare_digest(expected, self.headers["X-Sourceant-Signature"])
            received.append(json.loads(body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = InMemoryJobStore()
    monkeypatch.setenv(
        "DOMAIN_EVENTS_URL", f"http://127.0.0.1:{server.server_port}/events"
    )
    monkeypatch.setenv("DOMAIN_EVENTS_SECRET", "test-secret")
    monkeypatch.setattr("src.core.domain_events.enqueue", store.enqueue)
    services = ServiceRegistry()
    services.contribute(JobHandler, DomainEventDelivery(), "test")
    try:
        identifier = emit(type="review.failed", workspace="1", review="review-1")
        assert received == []
        Worker(store, BACKGROUND, name="test", services=services).work(max_jobs=1)
        assert store.read(identifier).state == "succeeded"
        assert received[0]["workspace"] == "1"
        assert received[0]["data"]["review"] == "review-1"
        assert received[0]["type"] == "review.failed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_unavailable_gateway_leaves_a_retryable_delivery(monkeypatch):
    monkeypatch.delenv("DOMAIN_EVENTS_URL", raising=False)
    result = DomainEventDelivery().run(None)
    assert not result.succeeded
    assert result.retry


def test_standalone_core_does_not_require_laravel(monkeypatch):
    monkeypatch.delenv("DOMAIN_EVENTS_URL", raising=False)
    assert emit(type="review.failed", workspace="1") is None


def test_event_joins_the_callers_transaction_and_rolls_back_with_it(monkeypatch):
    from sqlalchemy import create_engine
    from src.core.jobs.sql import SQLJobStore

    engine = create_engine("sqlite://")
    store = SQLJobStore(engine, create_schema=True)
    monkeypatch.setattr("src.core.domain_events.enqueue", store.enqueue)
    monkeypatch.setenv("DOMAIN_EVENTS_URL", "http://127.0.0.1/events")
    monkeypatch.setenv("DOMAIN_EVENTS_SECRET", "test-secret")
    with engine.connect() as connection:
        transaction = connection.begin()
        identifier = emit(type="review.failed", workspace="1", session=connection)
        transaction.rollback()
    assert store.read(identifier) is None
    engine.dispose()


def test_captured_credit_failure_emits_a_safe_classification(monkeypatch):
    from pathlib import Path
    from src.core.domain_events import report_failure

    sample = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "deepseek-insufficient-balance.json"
        ).read_text()
    )
    events = []
    monkeypatch.setattr(
        "src.core.domain_events.emit", lambda **event: events.append(event)
    )
    report_failure(
        Exception(sample["reported_error"]), workspace="1", review="review-1"
    )
    assert events[0]["type"] == "provider.credits_exhausted"
    assert "request_id" not in json.dumps(events)
    assert sample["reported_error"] not in json.dumps(events)


def test_failed_review_state_and_outbound_event_commit_together(monkeypatch):
    from sqlalchemy import create_engine
    from src.core.jobs.sql import SQLJobStore
    from src.core.review.work_store import SQLReviewWork

    engine = create_engine("sqlite://")
    jobs = SQLJobStore(engine, create_schema=True)
    work = SQLReviewWork(engine, create_schema=True)
    work.open(
        "run",
        workspace="1",
        repository="owner/repo",
        revision="head",
        plan={"parts": [{"id": "first"}]},
        maximum_calls=3,
    )
    monkeypatch.setenv("DOMAIN_EVENTS_URL", "http://127.0.0.1/events")
    monkeypatch.setenv("DOMAIN_EVENTS_SECRET", "test-secret")
    monkeypatch.setattr("src.core.domain_events.enqueue", jobs.enqueue)
    sent = []

    def failed(connection):
        sent.append(
            emit(type="review.failed", workspace="1", review="run", session=connection)
        )

    work.fail("run", on_failed=failed)
    assert work.progress("run")["state"] == "failed"
    assert jobs.read(sent[0]).payload["data"]["review"] == "run"
    engine.dispose()


def test_completion_rolls_back_when_its_coverage_event_cannot_be_saved(monkeypatch):
    import pytest
    from sqlalchemy import create_engine
    from src.core.review.work_store import SQLReviewWork

    engine = create_engine("sqlite://")
    work = SQLReviewWork(engine, create_schema=True)
    work.open(
        "run",
        workspace="1",
        repository="owner/repo",
        revision="head",
        plan={"parts": []},
        maximum_calls=3,
    )

    def unavailable(connection):
        raise RuntimeError("Outbox unavailable")

    with pytest.raises(RuntimeError, match="Outbox unavailable"):
        work.complete("run", on_completed=unavailable)
    assert work.progress("run")["state"] == "running"
    assert work.complete("run")
    engine.dispose()
