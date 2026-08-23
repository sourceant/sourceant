from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.config.settings import APP_ENV, STATELESS_MODE
from src.utils.logger import logger

router = APIRouter()

SERVICE_NAME = "sourceant-agent"

CHECK_TIMEOUT_SECONDS = 5.0

OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass
class Check:
    name: str
    status: str
    duration_ms: int
    detail: str | None = None

    def as_dict(self) -> dict:
        body = {"status": self.status, "duration_ms": self.duration_ms}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


def _version() -> str:
    from src.api.main import _read_version

    return _read_version()


def identity() -> dict:
    return {
        "service": SERVICE_NAME,
        "version": _version(),
        "environment": APP_ENV,
    }


def _check_database() -> tuple[str, str | None]:
    if STATELESS_MODE:
        return SKIPPED, "STATELESS_MODE is on"

    from src.config.db import get_engine

    engine = get_engine()
    if engine is None:
        return SKIPPED, "no engine configured"

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return OK, None


def _check_queue() -> tuple[str, str | None]:
    from src.events import dispatcher

    if dispatcher.q is None:
        return SKIPPED, "no queue mode configured"

    dispatcher.redis_conn.ping()
    return OK, f"{len(dispatcher.q)} queued"


def _check_graph() -> tuple[str, str | None]:
    from src.api.routes.topology import get_topology_repository
    from src.core.scope import Scope
    from src.core.topology import TopologyQuery

    repository = get_topology_repository()
    # Any scope reaches the store; nothing writes under this one.
    repository.search(
        TopologyQuery(scope=Scope.from_mapping({"workspace": "_health"}), limit=1)
    )
    return OK, type(repository).__name__


def _check_plugins() -> tuple[str, str | None]:
    from src.core.plugins import plugin_manager

    summary = plugin_manager.get_plugin_status_summary()
    counts = summary.get("status_counts", {})
    errored = counts.get("error", 0)
    described = ", ".join(
        f"{count} {status}" for status, count in sorted(counts.items())
    )

    if errored:
        return FAILED, described
    return OK, described or "none loaded"


CHECKS: dict[str, Callable[[], tuple[str, str | None]]] = {
    "database": _check_database,
    "queue": _check_queue,
    "graph": _check_graph,
    "plugins": _check_plugins,
}

# Never shut down with wait=True: a probe blocked on a dead socket cannot be
# cancelled, so waiting on it would hang the response that already timed it out.
_pool = ThreadPoolExecutor(max_workers=len(CHECKS) * 2, thread_name_prefix="health")


def _run(name: str, probe: Callable[[], tuple[str, str | None]]) -> Check:
    started = perf_counter()
    try:
        status, detail = probe()
    except Exception as error:
        logger.warning(f"Readiness check '{name}' failed: {error}", exc_info=True)
        status, detail = FAILED, str(error)
    elapsed = int((perf_counter() - started) * 1000)
    return Check(name=name, status=status, duration_ms=elapsed, detail=detail)


@router.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": OK, **identity()}


@router.get("/health/ready", tags=["health"])
async def ready() -> JSONResponse:
    futures = {name: _pool.submit(_run, name, probe) for name, probe in CHECKS.items()}

    results = []
    for name, future in futures.items():
        try:
            results.append(future.result(timeout=CHECK_TIMEOUT_SECONDS))
        except FutureTimeout:
            results.append(
                Check(
                    name=name,
                    status=FAILED,
                    duration_ms=int(CHECK_TIMEOUT_SECONDS * 1000),
                    detail=f"did not answer within {CHECK_TIMEOUT_SECONDS:g}s",
                )
            )

    degraded = [check.name for check in results if check.status == FAILED]

    return JSONResponse(
        status_code=503 if degraded else 200,
        content={
            "status": FAILED if degraded else OK,
            **identity(),
            "checks": {check.name: check.as_dict() for check in results},
        },
    )
