from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore, Lock
from contextlib import contextmanager
from time import monotonic

from src.config.settings import whole_number

MODEL_CONCURRENCY = whole_number("REVIEW_MODEL_CONCURRENCY", 6)
model_slots = BoundedSemaphore(MODEL_CONCURRENCY)
_active_lock = Lock()
_active_calls = 0


@contextmanager
def model_call(model):
    from src.core.review_performance import call_purpose, session_id
    from src.utils.logger import logger

    global _active_calls
    waiting = monotonic()
    with model_slots:
        started = monotonic()
        with _active_lock:
            _active_calls += 1
            active = _active_calls
        logger.info(
            "model_admitted session=%s purpose=%s model=%s active=%s capacity=%s wait_ms=%.1f",
            session_id(),
            call_purpose.get(),
            model,
            active,
            MODEL_CONCURRENCY,
            (started - waiting) * 1000,
        )
        error = ""
        try:
            yield
        except BaseException as failure:
            error = type(failure).__name__
            raise
        finally:
            with _active_lock:
                _active_calls -= 1
                active = _active_calls
            logger.info(
                "model_finished session=%s purpose=%s model=%s active=%s duration_ms=%.1f error=%s",
                session_id(),
                call_purpose.get(),
                model,
                active,
                (monotonic() - started) * 1000,
                error,
            )


def submit(pool, call, *args, **kwargs):
    return pool.submit(copy_context().run, call, *args, **kwargs)


def parallel_map(call, items, workers=6):
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futures = [submit(pool, call, item) for item in items]
        return [future.result() for future in futures]


class SharedReader:
    def __init__(self, read):
        self.read = read
        self.pending = {}
        self.lock = Lock()

    def __call__(self, path):
        return self.keyed(path, lambda: self.read(path))

    def keyed(self, path, call):
        with self.lock:
            future = self.pending.get(path)
            first = future is None
            if first:
                future = self.pending[path] = Future()
        if first:
            try:
                future.set_result(call())
            except BaseException as error:
                with self.lock:
                    self.pending.pop(path, None)
                future.set_exception(error)
        return future.result()

    def captured(self):
        with self.lock:
            pending = dict(self.pending)
        return {
            path: future.result()
            for path, future in pending.items()
            if future.done() and isinstance(future.result(), str)
        }
