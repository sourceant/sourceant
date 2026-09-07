"""Who gets to run next when more is queued than can be run.

Every case here is one a deployment meets the first time two customers are busy
at once, and none of them needs a database to be true.
"""

from src.core.jobs.fairness import share
from src.core.jobs.models import Candidate


def _queued(tenant: str, ids, key=None) -> list:
    return [Candidate(id=i, tenant=tenant, exclusive_key=key) for i in ids]


def test_two_tenants_with_the_same_backlog_get_the_same_share():
    """Neither of them enqueued their way to the front. Whoever asked first
    would otherwise take everything, which is what a single queue does now."""
    candidates = _queued("acme", range(1, 501)) + _queued("globex", range(501, 1001))

    chosen = share(candidates, busy={}, cap=3, budget=20)

    assert sum(1 for job in chosen if job < 501) == 3
    assert sum(1 for job in chosen if job >= 501) == 3


def test_a_tenant_with_one_job_does_not_wait_behind_another_tenants_backlog():
    """A single job must not wait behind five hundred belonging to somebody
    else, or one busy tenant is the only one anything happens for."""
    candidates = _queued("acme", range(1, 501)) + _queued("globex", [900])

    chosen = share(candidates, busy={}, cap=3, budget=4)

    assert 900 in chosen
    assert chosen.index(900) == 1


def test_a_tenant_already_at_its_cap_is_passed_over():
    """The cap counts what is running everywhere, not just what this worker
    started, or every worker would grant the same tenant its own full share."""
    candidates = _queued("acme", [1, 2, 3]) + _queued("globex", [4, 5])

    chosen = share(candidates, busy={"acme": 3}, cap=3, budget=10)

    assert chosen == (4, 5)


def test_one_tenants_own_work_stays_in_the_order_it_was_asked_for():
    """Fairness is between tenants. Within one, a queue that reordered itself
    would be a queue nobody could reason about."""
    candidates = _queued("acme", [7, 8, 9])

    chosen = share(candidates, busy={}, cap=5, budget=5)

    assert chosen == (7, 8, 9)


def test_the_tenant_served_longest_ago_goes_first():
    """Equal share only means anything if the turn actually rotates."""
    candidates = _queued("acme", [1]) + _queued("globex", [2])

    chosen = share(
        candidates,
        busy={},
        cap=3,
        budget=2,
        last_served={"acme": 100.0, "globex": 50.0},
    )

    assert chosen == (2, 1)


def test_a_job_cannot_be_taken_while_its_key_is_held():
    """Two runs for one repository would delete each other's checkout, so the
    second waits instead of both proceeding."""
    candidates = _queued("acme", [1], key="working-area:5")

    chosen = share(candidates, busy={}, cap=3, budget=5, held={"working-area:5"})

    assert chosen == ()


def test_two_queued_jobs_wanting_the_same_key_do_not_both_go():
    """The lock is taken after the claim, so choosing both would only mean one
    of them immediately handing its claim back."""
    candidates = _queued("acme", [1, 2], key="working-area:5")

    chosen = share(candidates, busy={}, cap=3, budget=5)

    assert chosen == (1,)


def test_a_held_key_does_not_stall_the_rest_of_that_tenants_work():
    """Otherwise one busy repository stops every other repository belonging to
    the same customer, which is a worse outage than the one being prevented."""
    candidates = [
        Candidate(id=1, tenant="acme", exclusive_key="working-area:5"),
        Candidate(id=2, tenant="acme"),
    ]

    chosen = share(candidates, busy={}, cap=3, budget=5, held={"working-area:5"})

    assert chosen == (2,)


def test_nothing_is_taken_without_budget():
    """A worker with no free slot must not claim work it cannot start, because
    a claim it never begins looks exactly like a job that hung."""
    assert share(_queued("acme", [1, 2]), busy={}, cap=3, budget=0) == ()


def test_a_full_rotation_that_takes_nothing_stops_rather_than_spinning():
    """Every candidate blocked is an ordinary state, not a reason to loop."""
    candidates = _queued("acme", [1, 2], key="k") + _queued("globex", [3], key="k")

    chosen = share(candidates, busy={}, cap=3, budget=50, held={"k"})

    assert chosen == ()
