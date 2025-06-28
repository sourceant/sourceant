from src.llms.gemini import Gemini
from src.llms.deepseek import DeepSeek
from src.llms.llm_interface import LLMInterface
from functools import lru_cache
from src.config.settings import LLM
from src.utils.logger import logger


@lru_cache(maxsize=None)
def llm() -> LLMInterface:
    """
    Factory function to get the language model instance.
    Uses lru_cache to ensure a single instance is created (singleton pattern).
    """
    if LLM == "gemini":
        logger.info("Using Gemini LLM.")
        return Gemini()
    elif LLM == "deepseek":
        logger.info("Using DeepSeek LLM.")
        return DeepSeek()
    else:
        raise NotImplementedError(f"LLM '{LLM}' not implemented.")
