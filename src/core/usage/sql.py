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

    def record(self, usage: ModelUsage) -> None:
        engine = get_engine()
        if engine is None:
            return
        try:
            with Session(engine) as session:
                session.add(
                    ModelUsageRecord(
                        model=usage.model,
                        purpose=usage.purpose,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost=usage.cost,
                        workspace=usage.workspace,
                        repository=usage.repository,
                        organization=usage.organization,
                        for_user=usage.user,
                    )
                )
                session.commit()
        except Exception:
            logger.warning("Could not record what a model call consumed", exc_info=True)
