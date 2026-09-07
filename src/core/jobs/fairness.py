"""Choosing which queued jobs to run next, and for whom.

This is the whole of the fairness rule, and it touches nothing. It
is given what is queued, what is already running and what may not overlap, and
it answers with job ids. That makes the rule readable on its own and testable
without a database, which matters because "one customer starved another" is not
something anybody wants to be debugging against live data.

The rule: every tenant gets an equal share, capped. Not a share proportional to
what they asked for, which would let whoever enqueued most win, and not first
come first served, which is the same thing said differently.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from .models import Candidate

#: Stands in for a tenant nothing has run for yet, so they sort ahead of
#: everyone who has already had a turn.
NEVER = float("-inf")


def share(
    candidates: Sequence[Candidate],
    *,
    busy: Mapping[str, int],
    cap: int,
    budget: int,
    last_served: Optional[Mapping[str, float]] = None,
    held: Optional[Iterable[str]] = None,
) -> tuple[int, ...]:
    """Which of `candidates` to claim now, in the order to claim them.

    `candidates` arrives in the order the database offered it, which is by
    priority and then by age, and that order is kept within each tenant so a
    tenant's own work stays first in first out.

    Tenants take turns, least recently served first. A tenant with one job
    waiting is served on the first rotation rather than behind another tenant's
    five hundred, which is the entire point.

    A job whose `exclusive_key` is already held is passed over rather than
    blocking its tenant, because otherwise one busy repository stops every
    other repository belonging to the same customer.
    """
    if budget <= 0 or cap <= 0 or not candidates:
        return ()

    served = last_served or {}
    taken_keys = set(held or ())
    by_tenant: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_tenant.setdefault(candidate.tenant, []).append(candidate)

    # Least recently served first. Where two tenants have never been served, or
    # were served at the same moment, the one whose oldest job is older goes
    # first, which keeps the answer stable rather than dependent on dict order.
    def turn(tenant: str) -> tuple[float, int]:
        return (served.get(tenant, NEVER), by_tenant[tenant][0].id)

    order = sorted(by_tenant, key=turn)
    cursors = {tenant: 0 for tenant in order}
    taken: dict[str, int] = {}
    chosen: list[int] = []

    while len(chosen) < budget:
        progressed = False
        for tenant in order:
            if len(chosen) >= budget:
                break
            if busy.get(tenant, 0) + taken.get(tenant, 0) >= cap:
                continue

            queue = by_tenant[tenant]
            index = cursors[tenant]
            while index < len(queue):
                candidate = queue[index]
                index += 1
                if candidate.exclusive_key and candidate.exclusive_key in taken_keys:
                    continue
                if candidate.exclusive_key:
                    taken_keys.add(candidate.exclusive_key)
                chosen.append(candidate.id)
                taken[tenant] = taken.get(tenant, 0) + 1
                progressed = True
                break
            cursors[tenant] = index

        # Nothing was takeable anywhere on a full rotation, so going round again
        # would only produce the same answer more slowly.
        if not progressed:
            break

    return tuple(chosen)
