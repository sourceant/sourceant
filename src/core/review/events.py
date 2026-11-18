from src.core.events import emit


def report_failure(
    error: Exception, *, workspace=None, repository=None, review="", session=None
) -> None:
    message = str(error).lower()
    type = "review.failed"
    if "insufficient_balance_error" in message or "insufficient balance" in message:
        type = "provider.credits_exhausted"
    else:
        from litellm.exceptions import AuthenticationError

        from src.llms.errors import LLMError

        cause = error
        visited = set()
        while cause is not None and id(cause) not in visited:
            visited.add(id(cause))
            if isinstance(cause, AuthenticationError):
                type = "provider.authentication_failed"
                break
            cause = cause.cause if isinstance(cause, LLMError) else cause.__cause__
    try:
        emit(
            type=type,
            workspace=str(workspace or ""),
            data={
                "repository": repository or "",
                "review": review,
                "provider": "",
                "model": "",
            },
            session=session,
        )
    except Exception:
        if session is not None:
            raise
        from src.utils.logger import logger

        logger.error("Could not persist review failure event", exc_info=True)
