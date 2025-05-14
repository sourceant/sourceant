import os
import json
from typing import Optional

from google import genai
from src.llms.llm_interface import LLMInterface
from src.prompts.prompts import Prompts
from src.utils.logger import logger
from src.models.code_review import CodeReview


class Gemini(LLMInterface):
    def __init__(self):
        """Initializes the Gemini client and model configuration."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY environment variable not set.")
            raise ValueError("GEMINI_API_KEY environment variable not set.")

        self.client = genai.Client(api_key=api_key)
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    def generate_code_review(
        self, diff: str, context: Optional[str] = None
    ) -> Optional[CodeReview]:
        """
        Generates a code review for a given diff using the Gemini model.

        Args:
            diff: The code diff to be reviewed.
            context: Optional additional context for the review.

        Returns:
            A CodeReview object if successful, None otherwise.
        """
        prompt = Prompts.REVIEW_PROMPT.format(
            diff=diff, context=context or "No context provided."
        )
        try:
            logger.info(
                f"Generating code review from Gemini model: {self.model_name}..."
            )
            config = genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CodeReview,
            )
            response = self.client.models.generate_content(
                model=f"models/{self.model_name}",
                contents=[prompt],
                config=config,
            )

            logger.info("Code review generated successfully.")

            if os.getenv("APP_ENV") in ["dev", "development", "debug"]:
                logger.debug(f"Raw response from Gemini: {response.text}")

            # The response text needs to be parsed into the CodeReview model.
            review_data = json.loads(response.text)
            return CodeReview(**review_data)

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON response from Gemini: {e}")
            if "response" in locals():
                logger.debug(f"Invalid response text: {response.text}")
            return None
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while generating code review from Gemini: {e}"
            )
            return None
