"""How long to wait before trying a job again.

Kept apart from both stores so the two cannot drift, and pure so the numbers
can be asserted rather than observed.
"""

from __future__ import annotations

import random

BASE_SECONDS = 5
CEILING_SECONDS = 3600


def after(
    attempt: int,
    *,
    base: int = BASE_SECONDS,
    ceiling: int = CEILING_SECONDS,
    jitter: bool = True,
) -> int:
    """Seconds to wait after `attempt` failures.

    Doubling, up to a ceiling, with jitter. The jitter is not decoration: a
    provider that refused ten jobs at once refused them at the same instant, and
    without it all ten would come back at the same instant too and be refused
    again together.
    """
    if attempt <= 0:
        return 0
    doubled = min(base * (2 ** (attempt - 1)), ceiling)
    if not jitter:
        return doubled
    return random.randint(doubled // 2 or 1, doubled)
