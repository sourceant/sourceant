from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore, Lock

from src.config.settings import whole_number


MODEL_CONCURRENCY = whole_number("REVIEW_MODEL_CONCURRENCY", 6)
model_slots = BoundedSemaphore(MODEL_CONCURRENCY)


def submit(pool, call, *args):
    return pool.submit(copy_context().run, call, *args)


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
        with self.lock:
            future = self.pending.get(path)
            first = future is None
            if first:
                future = self.pending[path] = Future()
        if first:
            try:
                future.set_result(self.read(path))
            except BaseException as error:
                future.set_exception(error)
        return future.result()
