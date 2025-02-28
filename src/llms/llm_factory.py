from src.llms.gemini import Gemini
from src.llms.deepseek import DeepSeek
from src.llms.llm_interface import LLMInterface
from src.utils.logger import logger
import os
from dotenv import load_dotenv

load_dotenv()


def llm() -> LLMInterface:
    """Creates an LLM instance based on the LLME environment variable."""
    llm_type = os.getenv("LLM", "gemini")
    if llm_type == "gemini":
        logger.info("Using Gemini LLM.")
        return Gemini()
    elif llm_type == "deepseek":
        logger.info("Using DeepSeek LLM.")
        return DeepSeek()
    else:
        logger.error(f"Unknown LLM type: {llm_type}")
        raise ValueError(f"Unknown LLM type: {llm_type}")
