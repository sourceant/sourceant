from src.llms.litellm_provider import LiteLLMProvider
from src.llms.llm_interface import LLMInterface
from functools import lru_cache
from src.config.settings import LLM_MODEL, LLM_TOKEN_LIMIT, LLM_UPLOADS_ENABLED
from src.utils.logger import logger


@lru_cache(maxsize=None)
def llm() -> LLMInterface:
    """
    Factory function to get the language model instance.
    Uses lru_cache to ensure a single instance is created (singleton pattern).
    """
    logger.info(f"Initializing LLM with model: {LLM_MODEL}")
    return LiteLLMProvider(
        model=LLM_MODEL,
        token_limit=LLM_TOKEN_LIMIT,
        uploads_enabled=LLM_UPLOADS_ENABLED,
    )
