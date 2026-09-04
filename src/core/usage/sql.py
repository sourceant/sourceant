"""Keeping what was consumed in the deployment's own database."""

from __future__ import annotations

from sqlmodel import Session

from src.config.db import get_engine
from src.models.model_usage import ModelUsageRecord
from src.utils.logger import logger

from .models import ModelUsage


class SQLUsageRecorder:
    """Writes one row per model call.

    A failure here is swallowed on purpose: the call it describes has already
    happened and its answer is on its way back to somebody, so failing to write
    the bill must not turn a completed review into an error.
    """

    def __init__(self, *, create_schema: bool = False) -> None:
        self._created = not create_schema

    def _ready(self, engine) -> None:
        """Build the table where nothing else has.

        Migrations are what a deployment runs, but a personal machine started
        with `sourceant serve` runs none, and a missing table would drop every
        row while only ever saying so in a log.
        """
        if self._created:
            return
        from src.models.model_usage import ModelUsageRecord

        ModelUsageRecord.__table__.create(engine, checkfirst=True)
        self._created = True

    def record(self, usage: ModelUsage) -> None:
        engine = get_engine()
        if engine is None:
            return
        self._ready(engine)
        try:
            with Session(engine) as session:
                session.add(
                    ModelUsageRecord(
                        provider=usage.provider,
                        model=usage.model,
                        purpose=usage.purpose,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        reported_total=usage.reported_total,
                        cost_micro=usage.cost_micro,
                        currency=usage.currency,
                        workspace=usage.workspace,
                        repository=usage.repository,
                        organization=usage.organization,
                        for_user=usage.user,
                    )
                )
                session.commit()
        except Exception:
            logger.warning("Could not record what a model call consumed", exc_info=True)
