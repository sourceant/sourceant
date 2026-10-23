from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
from time import monotonic
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

from src.core.parallel import SharedReader, submit
from src.utils.logger import logger

_session = ContextVar("review_session", default=None)
call_purpose = ContextVar("model_call_purpose", default="")
reading_identity = ContextVar("review_reading_identity", default="")
expert_identity = ContextVar("review_expert_identity", default="general")


class ReviewSession:
    def __init__(self, configuration=None):
        from src.core.review_budget import ReviewBudget

        self.id = uuid4().hex
        self.budget = ReviewBudget(configuration)
        self.values = SharedReader(None)
        self.pool = ThreadPoolExecutor(max_workers=2)


def shared(key, call):
    session = _session.get()
    if session is None:
        return call()
    return session.values.keyed(key, call)


def background(call, *args, **kwargs):
    session = _session.get()
    if session is None:
        raise RuntimeError("Background preparation needs a review session")
    return submit(session.pool, call, *args, **kwargs)


@contextmanager
def stage(name, **details):
    started = monotonic()
    session = _session.get()
    status = "success"
    reading_token = expert_token = None
    if name in {"discovery", "evaluation"}:
        reading_token = reading_identity.set(
            f"{details['participant']}:{details['repetition']}"
        )
    elif name == "expert":
        expert_token = expert_identity.set(details["skill"])
    try:
        yield
    except BaseException:
        status = "failed"
        raise
    finally:
        logger.info(
            "review_stage session=%s stage=%s status=%s duration_ms=%.1f details=%s",
            session.id if session else "",
            name,
            status,
            (monotonic() - started) * 1000,
            details,
        )
        if reading_token is not None:
            reading_identity.reset(reading_token)
        if expert_token is not None:
            expert_identity.reset(expert_token)


def review_session(call):
    if inspect.iscoroutinefunction(call):

        @wraps(call)
        async def asynchronous(*args, **kwargs):
            if _session.get() is not None:
                return await call(*args, **kwargs)
            from src.core.settings.configuration import Configuration
            from src.core.review_budget import ReviewBudgetExceeded

            repository = kwargs.get("repository_full_name")
            if not repository and len(args) > 1:
                repository = f"{args[1].owner}/{args[1].name}"
            session = ReviewSession(
                Configuration(
                    repository=repository,
                    workspace=kwargs.get("workspace"),
                    user=kwargs.get("user"),
                )
            )
            token = _session.set(session)
            try:
                result = await call(*args, **kwargs)
                if session.budget.exhausted:
                    raise ReviewBudgetExceeded(
                        "The review exceeded its budget; no partial review was published."
                    )
                return result
            finally:
                session.pool.shutdown(wait=True)
                _session.reset(token)

        return asynchronous

    @wraps(call)
    def run(*args, **kwargs):
        if _session.get() is not None:
            result = call(*args, **kwargs)
            if _session.get().budget.exhausted:
                from src.core.review_budget import ReviewBudgetExceeded

                raise ReviewBudgetExceeded(
                    "The review exceeded its budget; no partial review was published."
                )
            return result
        configuration = None
        repository, head = kwargs.get("repository", ""), kwargs.get("head", "")
        if len(args) > 1:
            change = getattr(args[1], "changes", args[1])
            configuration = getattr(change, "configuration", None)
            scope = getattr(change, "scope", None)
            if scope is not None:
                repository = repository or scope.get("repository") or ""
            head = head or getattr(change, "revision", "")
        session = ReviewSession(configuration)
        token = _session.set(session)
        logger.info(
            "review_started session=%s repository=%s head=%s",
            session.id,
            repository,
            head,
        )
        try:
            with stage("review", repository=repository, head=head):
                try:
                    result = call(*args, **kwargs)
                    if session.budget.exhausted:
                        from src.core.review_budget import ReviewBudgetExceeded

                        raise ReviewBudgetExceeded(
                            "The review exceeded its budget; no partial review was published."
                        )
                    return result
                finally:
                    session.pool.shutdown(wait=True)
        finally:
            _session.reset(token)

    return run


def session_id():
    session = _session.get()
    return session.id if session else ""


def current_budget():
    session = _session.get()
    return session.budget if session else None


def model_request(call):
    @wraps(call)
    def run(self, purpose, *args, **kwargs):
        token = call_purpose.set(purpose)
        try:
            with stage(purpose, model=self.model):
                return call(self, purpose, *args, **kwargs)
        finally:
            call_purpose.reset(token)

    return run
