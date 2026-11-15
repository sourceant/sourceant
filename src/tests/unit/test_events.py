import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from src.core.event_delivery import EventDelivery
from src.core.events import emit
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
    monkeypatch.setenv("EVENTS_URL", f"http://127.0.0.1:{server.server_port}/events")
    monkeypatch.setenv("EVENTS_SECRET", "test-secret")
    monkeypatch.setattr("src.core.event_delivery.enqueue", store.enqueue)
    services = ServiceRegistry()
    services.contribute(JobHandler, EventDelivery(), "test")
    try:
        identifier = emit(
            type="review.failed", workspace="1", data={"review": "review-1"}
        )["sourceant.events.forward"]
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
    monkeypatch.delenv("EVENTS_URL", raising=False)
    result = EventDelivery().run(None)
    assert not result.succeeded
    assert result.retry


def test_standalone_core_does_not_require_laravel(monkeypatch):
    monkeypatch.delenv("EVENTS_URL", raising=False)
    assert emit(type="review.failed", workspace="1")["sourceant.events.forward"] is None


def test_event_joins_the_callers_transaction_and_rolls_back_with_it(monkeypatch):
    from sqlalchemy import create_engine
    from src.core.jobs.sql import SQLJobStore

    engine = create_engine("sqlite://")
    store = SQLJobStore(engine, create_schema=True)
    monkeypatch.setattr("src.core.event_delivery.enqueue", store.enqueue)
    monkeypatch.setenv("EVENTS_URL", "http://127.0.0.1/events")
    monkeypatch.setenv("EVENTS_SECRET", "test-secret")
    with engine.connect() as connection:
        transaction = connection.begin()
        identifier = emit(type="review.failed", workspace="1", session=connection)[
            "sourceant.events.forward"
        ]
        transaction.rollback()
    assert store.read(identifier) is None
    engine.dispose()


def test_captured_credit_failure_emits_a_safe_classification(monkeypatch):
    from pathlib import Path
    from src.core.review.events import report_failure

    sample = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "deepseek-insufficient-balance.json"
        ).read_text()
    )
    events = []
    monkeypatch.setattr(
        "src.core.review.events.emit", lambda **event: events.append(event)
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
    monkeypatch.setenv("EVENTS_URL", "http://127.0.0.1/events")
    monkeypatch.setenv("EVENTS_SECRET", "test-secret")
    monkeypatch.setattr("src.core.event_delivery.enqueue", jobs.enqueue)
    sent = []

    def failed(connection):
        sent.append(
            emit(
                type="review.failed",
                workspace="1",
                data={"review": "run"},
                session=connection,
            )
        )

    work.fail("run", on_failed=failed)
    assert work.progress("run")["state"] == "failed"
    assert (
        jobs.read(sent[0]["sourceant.events.forward"]).payload["data"]["review"]
        == "run"
    )
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


def test_local_events_use_existing_subscribers_without_a_gateway(monkeypatch):
    from src.core.plugins.event_hooks import EventHooks
    import src.core.events as events

    hooks = EventHooks()
    observed = []
    hooks.subscribe_to_events(
        "metrics", lambda type, event: observed.append(event), ["worker.started"]
    )
    monkeypatch.setattr(events, "event_hooks", hooks)
    monkeypatch.delenv("EVENTS_URL", raising=False)
    events.emit(type="worker.started", data={"worker": "background"})
    assert observed[0]["type"] == "worker.started"
    assert observed[0]["data"] == {"worker": "background"}


def test_forwarding_is_a_listener_and_receives_the_review_transaction(monkeypatch):
    from sqlalchemy import create_engine
    from src.core.event_delivery import forward
    from src.core.jobs.sql import SQLJobStore
    from src.core.plugins.event_hooks import EventHooks
    import src.core.events as events

    hooks = EventHooks()
    hooks.subscribe_to_events(
        "sourceant.events.forward", forward, ["review.failed"], transactional=True
    )
    observed = []
    hooks.subscribe_to_events(
        "audit", lambda type, event: observed.append(event), ["review.failed"]
    )
    monkeypatch.setattr(events, "event_hooks", hooks)
    engine = create_engine("sqlite://")
    store = SQLJobStore(engine, create_schema=True)
    monkeypatch.setattr("src.core.event_delivery.enqueue", store.enqueue)
    monkeypatch.setenv("EVENTS_URL", "http://127.0.0.1/events")
    monkeypatch.setenv("EVENTS_SECRET", "test-secret")
    with engine.begin() as connection:
        identifier = events.emit(
            type="review.failed", workspace="1", session=connection
        )["sourceant.events.forward"]
    assert store.read(identifier).payload["id"] == observed[0]["id"]
    engine.dispose()


def test_listener_failure_rolls_back_completion_and_its_queued_delivery(monkeypatch):
    import pytest
    from sqlalchemy import create_engine
    from src.core.event_delivery import forward
    from src.core.jobs.sql import SQLJobStore
    from src.core.plugins.event_hooks import EventHooks
    from src.core.review.work_store import SQLReviewWork
    import src.core.events as events

    engine = create_engine("sqlite://")
    jobs = SQLJobStore(engine, create_schema=True)
    work = SQLReviewWork(engine, create_schema=True)
    work.open(
        "run",
        workspace="1",
        repository="owner/repo",
        revision="head",
        plan={"parts": []},
        maximum_calls=3,
    )
    hooks = EventHooks()
    hooks.subscribe_to_events(
        "sourceant.events.forward",
        forward,
        ["review.coverage_incomplete"],
        transactional=True,
    )

    def unavailable(type, event):
        raise RuntimeError("Listener unavailable")

    hooks.subscribe_to_events("audit", unavailable, ["review.coverage_incomplete"])
    monkeypatch.setattr(events, "event_hooks", hooks)
    monkeypatch.setattr("src.core.event_delivery.enqueue", jobs.enqueue)
    monkeypatch.setenv("EVENTS_URL", "http://127.0.0.1/events")
    monkeypatch.setenv("EVENTS_SECRET", "test-secret")
    with pytest.raises(RuntimeError, match="Listener unavailable"):
        work.complete(
            "run",
            on_completed=lambda connection: events.emit(
                type="review.coverage_incomplete", workspace="1", session=connection
            ),
        )
    assert work.progress("run")["state"] == "running"
    assert jobs.claim("background", "test", 1) == []
    engine.dispose()


def test_sync_dispatch_rejects_async_listeners_before_running_any_listener():
    import pytest
    from src.core.plugins.event_hooks import EventHooks

    hooks = EventHooks()
    observed = []
    hooks.subscribe_to_events(
        "sync", lambda type, event: observed.append(event), ["worker.started"]
    )

    async def asynchronous(type, event):
        pass

    hooks.subscribe_to_events("async", asynchronous, ["worker.started"])
    with pytest.raises(TypeError, match="synchronous listeners"):
        hooks.dispatch_event("worker.started", {})
    assert observed == []


def test_review_failures_are_available_to_local_listeners_without_a_workspace(
    monkeypatch,
):
    from src.core.plugins.event_hooks import EventHooks
    from src.core.review.events import report_failure
    import src.core.events as events

    hooks = EventHooks()
    observed = []
    hooks.subscribe_to_events(
        "audit", lambda type, event: observed.append(event), ["review.failed"]
    )
    monkeypatch.setattr(events, "event_hooks", hooks)
    report_failure(RuntimeError("Review interrupted"))
    assert observed[0]["type"] == "review.failed"
    assert observed[0]["workspace"] == ""
    assert "Review interrupted" not in json.dumps(observed)


def test_async_broadcast_and_sync_dispatch_share_the_listener_registry():
    import asyncio
    from src.core.plugins.event_hooks import EventHooks

    hooks = EventHooks()
    observed = []

    def transactional(type, event, *, session=None):
        observed.append((type, event, session))

    hooks.subscribe_to_events(
        "audit", transactional, ["worker.started"], transactional=True
    )
    hooks.dispatch_event("worker.started", {"worker": "first"}, session="transaction")
    asyncio.run(hooks.broadcast_event("worker.started", {"worker": "second"}))
    assert observed == [
        ("worker.started", {"worker": "first"}, "transaction"),
        ("worker.started", {"worker": "second"}, None),
    ]
