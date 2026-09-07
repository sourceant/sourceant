"""The `sourceant work` command: one process doing background work.

One job at a time. More at once is more of these, not more threads, because a
worker has to be able to stop work that has gone on too long and a thread
cannot be stopped. Running several is a matter for whatever starts them.
"""

import click

from src.config.db import get_engine
from src.core.jobs import LANES, WITHIN_MINUTES, WITHIN_SECONDS, JobHandler, job_store
from src.core.jobs.sweep import Sweeper
from src.core.jobs.worker import Worker
from src.core.services import service_registry
from src.events.delivery import Deliveries
from src.utils.logger import logger


@click.command(name="work")
@click.option(
    "--lane",
    default=WITHIN_SECONDS,
    type=click.Choice(LANES),
    help="Which promise this worker keeps. Lanes are named by how long work in "
    "them may wait.",
)
@click.option("--name", default="", help="What this worker calls itself in the logs.")
@click.option(
    "--poll", default=1.0, help="Seconds between looks at the queue when it is empty."
)
@click.option(
    "--max-jobs",
    default=0,
    help="Stop after this many jobs so a fresh process takes over. Zero never stops.",
)
@click.option(
    "--max-time",
    default=0.0,
    help="Stop after this many seconds, for the same reason. Zero never stops.",
)
def work_command(lane, name, poll, max_jobs, max_time):
    """Claim background work for one lane and do it."""
    if get_engine() is None:
        raise click.ClickException(
            "There is no database here, so there is no queue to work. "
            "Background work runs in the request that asked for it instead."
        )

    store = job_store()
    sweeper = Sweeper(store)
    service_registry.contribute(JobHandler, sweeper, "sourceant_core")
    service_registry.contribute(JobHandler, Deliveries(), "sourceant_core")
    if lane == WITHIN_MINUTES:
        sweeper.arrange(store)

    worker = Worker(store, lane, name=name, poll_seconds=poll)
    worker.attend()
    logger.info(f"{worker.name} is working the {lane} lane")
    done = worker.work(max_jobs=max_jobs, max_time=max_time)
    logger.info(f"{worker.name} stopped after {done} jobs")
