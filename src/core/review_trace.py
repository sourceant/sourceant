from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
import hashlib
import inspect
import json
from threading import Lock
from time import monotonic
from uuid import uuid4
from typing import Protocol, runtime_checkable

from src.core.review_performance import (
    expert_identity,
    reading_identity,
    session_id,
)
from src.core.services import service_registry
from src.utils.logger import logger


class TraceKind(str, Enum):
    CONTEXT = "context"
    FINDING = "finding"
    EXCHANGE = "exchange"
    SUMMARY = "summary"


@dataclass(frozen=True)
class ReviewTraceEvent:
    kind: TraceKind
    stage: str
    action: str
    session: str
    participant: str
    expert: str
    sequence: int
    elapsed_ms: float
    path: str = ""
    start_line: int | None = None
    end_line: int | None = None
    finding_id: str = ""
    reason: str = ""
    characters: int | None = None
    response_id: str = ""
    finish_reason: str = ""
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    input_count: int | None = None
    output_count: int | None = None
    request_id: str = ""
    message_fingerprint: str = ""
    completion_limit: int | None = None


@runtime_checkable
class ReviewTraceSink(Protocol):
    def record(self, event: ReviewTraceEvent) -> None: ...


@dataclass
class _TraceState:
    sink: ReviewTraceSink
    identities: dict = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)
    sequence: int = 0
    started: float = field(default_factory=monotonic)


_sink: ContextVar[_TraceState | None] = ContextVar("review_trace", default=None)
_request_id = ContextVar("review_trace_request", default="")


@contextmanager
def _binding(self):
    token = None
    request_token = None
    if _sink.get() is None:
        try:
            sink = getattr(self, "services", service_registry).resolve(ReviewTraceSink)
            token = _sink.set(_TraceState(sink))
            request_token = _request_id.set("")
        except LookupError:
            pass
        except Exception as error:
            logger.warning("Review trace resolution failed: %s", type(error).__name__)
    try:
        yield
    finally:
        if token is not None:
            _sink.reset(token)
        if request_token is not None:
            _request_id.reset(request_token)


def traced_review(call):
    if inspect.iscoroutinefunction(call):

        @wraps(call)
        async def asynchronous(self, *args, **kwargs):
            with _binding(self):
                return await call(self, *args, **kwargs)

        return asynchronous

    @wraps(call)
    def run(self, *args, **kwargs):
        with _binding(self):
            return call(self, *args, **kwargs)

    return run


def emit(kind: TraceKind, stage: str, action: str, **details) -> None:
    state = _sink.get()
    if state is None:
        return
    try:
        with state.lock:
            state.sequence += 1
            sequence = state.sequence
        event = ReviewTraceEvent(
            kind,
            stage,
            action,
            session_id(),
            reading_identity.get(),
            expert_identity.get(),
            sequence,
            (monotonic() - state.started) * 1000,
            request_id=_request_id.get(),
            **details,
        )
        state.sink.record(event)
    except Exception as error:
        logger.warning("Review trace sink failed: %s", type(error).__name__)


def finding(stage: str, action: str, suggestion, *, reason="") -> None:
    state = _sink.get()
    if state is None:
        return
    try:
        key = reading_identity.get(), id(suggestion)
        with state.lock:
            if key not in state.identities:
                state.identities[key] = suggestion, uuid4().hex
            identity = state.identities[key][1]
        emit(
            TraceKind.FINDING,
            stage,
            action,
            path=suggestion.file_name,
            start_line=suggestion.start_line,
            end_line=suggestion.end_line,
            finding_id=identity,
            reason=reason,
        )
    except Exception as error:
        logger.warning("Review finding trace failed: %s", type(error).__name__)


def context(
    stage: str,
    action: str,
    path: str,
    *,
    characters=None,
    reason="",
    start_line=None,
    end_line=None,
):
    emit(
        TraceKind.CONTEXT,
        stage,
        action,
        path=path,
        characters=characters,
        reason=reason,
        start_line=start_line,
        end_line=end_line,
    )


def exchange(response, purpose: str, *, action="completed"):
    if _sink.get() is None:
        return
    try:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = (
            details.get("reasoning_tokens")
            if isinstance(details, dict)
            else getattr(details, "reasoning_tokens", None)
        )
        emit(
            TraceKind.EXCHANGE,
            purpose,
            action,
            response_id=getattr(response, "id", "") or "",
            finish_reason=response.choices[0].finish_reason or "",
            completion_tokens=getattr(usage, "completion_tokens", None),
            reasoning_tokens=reasoning if isinstance(reasoning, int) else None,
        )
    except Exception as error:
        logger.warning("Review exchange trace failed: %s", type(error).__name__)


def request(payload, purpose):
    if _sink.get() is None:
        return
    try:
        identifier = uuid4().hex
        _request_id.set(identifier)
        normalized = []
        for message in payload.get("messages", ()):
            item = dict(message)
            if isinstance(item.get("content"), list):
                item["content"] = "\n".join(
                    part.get("text", "") for part in item["content"]
                )
            normalized.append(item)
        fingerprint = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        emit(
            TraceKind.EXCHANGE,
            purpose,
            "requested",
            message_fingerprint=fingerprint,
            completion_limit=payload.get(
                "max_tokens", payload.get("max_completion_tokens")
            ),
        )
    except Exception as error:
        logger.warning("Review request trace failed: %s", type(error).__name__)


def completed(stage, inputs, outputs):
    emit(
        TraceKind.SUMMARY, stage, "completed", input_count=inputs, output_count=outputs
    )
