import os
from typing import Optional, List

from google import genai
from src.llms.llm_interface import LLMInterface
from src.prompts.prompts import Prompts
from src.utils.logger import logger
from src.models.code_review import CodeReview, Verdict, CodeSuggestion


class Gemini(LLMInterface):
    def __init__(self):
        """Initializes the Gemini client and model configuration."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY environment variable not set.")
            raise ValueError("GEMINI_API_KEY environment variable not set.")

        self.client = genai.Client(api_key=api_key)
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._token_limit = int(os.getenv("GEMINI_TOKEN_LIMIT", 1000000))

    @property
    def token_limit(self) -> int:
        return self._token_limit

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
                # The response is now a CodeReview object, not a response with .text
                logger.debug(f"Raw response from Gemini: {response}")

            review = response.parsed

            # Override verdict based on scores
            if review and review.scores:
                total_score = (
                    review.scores.correctness
                    + review.scores.clarity
                    + review.scores.maintainability
                    + review.scores.security
                    + review.scores.performance
                )
                average_score = total_score / 5.0

                # If the average score is less than 5 (50%), request changes.
                if average_score < 5:
                    review.verdict = Verdict.REQUEST_CHANGES
                else:
                    review.verdict = Verdict.APPROVE

            return review
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while generating code review from Gemini: {e}"
            )
            return None

    def generate_summary(self, suggestions: List[CodeSuggestion]) -> str:
        if not suggestions:
            return "Great work! I have no suggestions for improvement."

        suggestions_text = ""
        for s in suggestions:
            suggestions_text += f"- **File:** `{s.file_name}` (Line: {s.line})\n"
            suggestions_text += f"  - **Comment:** {s.comment}\n"

        prompt = Prompts.SUMMARIZE_REVIEW_PROMPT.format(suggestions=suggestions_text)

        response = self.client.models.generate_content(
            model=f"models/{self.model_name}", contents=[prompt]
        )
        return response.text

    def generate_text(self, prompt: str) -> str:
        """Generates a simple text response for a given prompt."""
        try:
            response = self.client.models.generate_content(
                model=f"models/{self.model_name}", contents=[prompt]
            )
            return response.text
        except Exception as e:
            logger.error(f"An error occurred during text generation: {e}")
            return ""

    def count_tokens(self, text: str) -> int:
        """Counts the number of tokens in a given text string."""
        response = self.client.models.count_tokens(
            model=f"models/{self.model_name}", contents=[text]
        )
        return response.total_tokens
