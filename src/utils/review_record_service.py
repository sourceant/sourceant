from typing import Optional

from sqlmodel import select, col
from src.config.db import get_session
from src.config.settings import STATELESS_MODE
from src.models.review_record import ReviewRecord
from src.utils.logger import logger


def get_last_reviewed_sha(repo_full_name: str, pr_number: int) -> Optional[str]:
    if STATELESS_MODE:
        return None

    try:
        with next(get_session()) as session:
            statement = (
                select(ReviewRecord)
                .where(ReviewRecord.repository_full_name == repo_full_name)
                .where(ReviewRecord.pr_number == pr_number)
                .where(ReviewRecord.status == "completed")
                .order_by(col(ReviewRecord.created_at).desc())
                .limit(1)
            )
            record = session.exec(statement).first()
            if record:
                return record.reviewed_head_sha
            return None
    except Exception as e:
        logger.warning(f"Failed to query last reviewed SHA: {e}")
        return None


def save_review_record(
    repo_full_name: str, pr_number: int, head_sha: str, base_sha: str
) -> None:
    if STATELESS_MODE:
        return

    try:
        record = ReviewRecord(
            repository_full_name=repo_full_name,
            pr_number=pr_number,
            reviewed_head_sha=head_sha,
            reviewed_base_sha=base_sha,
            status="completed",
        )
        record.save()
        logger.info(
            f"Saved review record for {repo_full_name}#{pr_number} at {head_sha}"
        )
    except Exception as e:
        logger.warning(f"Failed to save review record: {e}")
