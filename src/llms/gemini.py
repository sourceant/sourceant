from src.llms.llm_interface import LLMInterface
from src.prompts.prompts import Prompts
from src.utils.logger import logger
from src.models.code_review import CodeReview
from google import genai
import os
import json


class Gemini(LLMInterface):
    def __init__(self):
        # Ensure the GEMINI_API_KEY environment variable is set
        if not os.getenv("GEMINI_API_KEY"):
            logger.error("GEMINI_API_KEY environment variable not set.")
            raise ValueError("GEMINI_API_KEY environment variable not set.")
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    def generate_code_review(self, diff: str, context: str = "None") -> CodeReview:
        prompt = Prompts.REVIEW_PROMPT.format(diff=diff, context=context)
        try:
            logger.info("Generating code review from Gemini...")
            response = self.client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[prompt],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": CodeReview,
                },
            )
            logger.info("Code review generated successfully.")
            if os.getenv("APP_ENV") in ["dev", "development", "debug"]:
                logger.debug(f"Code review from Gemini: {response.text}")
                try:
                    return CodeReview(**json.loads(response.text))
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON from Gemini: {e}")
                    return None
        except Exception as e:
            logger.error(f"Error generating code review from Gemini: {e}")
            return f"Error generating code review from Gemini: {e}"
