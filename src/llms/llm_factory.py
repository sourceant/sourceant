from src.llms.litellm_provider import LiteLLMProvider
from src.llms.llm_interface import LLMInterface
from functools import lru_cache
from src.config.settings import LLM_MODEL, LLM_TOKEN_LIMIT
from src.utils.logger import logger


def llm(
    *,
    repository: str | None = None,
    workspace: str | None = None,
    user: str | None = None,
) -> LLMInterface:
    from src.core.workspace import workspace_holding

    workspace = workspace or (workspace_holding(repository) if repository else None)
    return _provider(repository, workspace, user)


@lru_cache(maxsize=128)
def _provider(repository, workspace, user):
    logger.info(f"Initializing LLM with model: {LLM_MODEL}")
    return LiteLLMProvider(
        model=LLM_MODEL,
        token_limit=LLM_TOKEN_LIMIT,
        attribution={"repository": repository, "workspace": workspace, "user": user},
    )
