"""The tidying, which asks for its own next run so nothing has to schedule it."""

from src.core.jobs.memory import InMemoryJobStore
from src.core.jobs.models import WITHIN_MINUTES
from src.core.jobs.sweep import SWEEP_KIND, SWEEP_SLOT, Sweeper


def test_a_sweep_asks_for_the_next_one_so_nothing_has_to_schedule_it():
    """There is no scheduler anywhere in this estate, so a tidy that had to be
    scheduled would simply never run."""
    store = InMemoryJobStore()
    sweeper = Sweeper(store, every_seconds=3600)

    assert sweeper.run().succeeded is True

    waiting = store.pending(WITHIN_MINUTES)
    assert [job.kind for job in waiting] == [SWEEP_KIND]
    assert waiting[0].dedupe_slot == SWEEP_SLOT


def test_two_sweeps_cannot_be_queued_at_once():
    """It runs every hour for the life of the deployment, so a duplicate would
    not stay a duplicate for long."""
    store = InMemoryJobStore()
    sweeper = Sweeper(store)

    first = sweeper.arrange()
    second = sweeper.arrange()

    assert first == second
    assert len(store.pending(WITHIN_MINUTES)) == 1


def test_a_sweep_that_cannot_tidy_says_so_and_is_tried_again():
    """Tidying is not urgent, but a sweep that failed silently would be the end
    of all tidying, because each one asks for the next."""

    class Broken(InMemoryJobStore):
        def prune(self, keep_finished_for_days: int = 14) -> int:
            raise RuntimeError("no")

    outcome = Sweeper(Broken()).run()

    assert outcome.succeeded is False
    assert outcome.retry is True
