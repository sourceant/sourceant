"""Run several provider calls at once without opening a flood of them.

Asking the provider for one repository at a time is slow when a scope covers a
dozen; asking for all of them at once trips the provider's secondary rate
limits. This keeps a few in flight and no more.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Sequence

DEFAULT_LIMIT = 8


async def gather_bounded(
    calls: Sequence[Callable[[], Awaitable[Any]]],
    limit: int = DEFAULT_LIMIT,
) -> list[Any]:
    """Await every call, at most `limit` of them at a time, keeping their order."""
    semaphore = asyncio.Semaphore(limit)

    async def _run(call: Callable[[], Awaitable[Any]]):
        async with semaphore:
            return await call()

    return await asyncio.gather(*[_run(call) for call in calls])
