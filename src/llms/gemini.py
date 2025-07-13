import os
from pathlib import Path
from typing import Optional, List, Union
import mimetypes

from google import genai
from src.llms.llm_interface import LLMInterface
from src.prompts.prompts import Prompts
from src.utils.diff_parser import ParsedDiff
from src.utils.logger import logger
from src.config.settings import LLM_UPLOADS_ENABLED
from src.models.code_review import (
    CodeReview,
    Verdict,
    CodeSuggestion,
    CodeReviewSummary,
)


class Gemini(LLMInterface):
    def __init__(self):
        """Initializes the Gemini client and model configuration."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY environment variable not set.")
            raise ValueError("GEMINI_API_KEY environment variable not set.")

        self.client = genai.Client(api_key=api_key)
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.uploads_enabled = LLM_UPLOADS_ENABLED
        self._token_limit = int(os.getenv("GEMINI_TOKEN_LIMIT", 1000000))

    @property
    def token_limit(self) -> int:
        return self._token_limit

    def generate_code_review(
        self,
        diff: str,
        parsed_files: Optional[List[ParsedDiff]] = None,
        file_paths: Optional[List[str]] = None,
    ) -> Optional[CodeReview]:
        """
        Generates a code review for a given diff using the Gemini model.
        This version uploads the full content of changed files for better context.

        Args:
            diff: The code diff to be reviewed.
            file_paths: A list of local file paths to be uploaded for context.

        Returns:
            A CodeReview object if successful, None otherwise.
        """
        prompt_parts: List[Union[str, genai.types.File]] = []
        uploaded_files: List[genai.types.File] = []

        upload_successful = False
        if self.uploads_enabled and file_paths:
            logger.info(f"Attempting to upload {len(file_paths)} files for context...")
            all_files_uploaded = True
            for file_path_str in file_paths:
                file_path = Path(file_path_str)
                if not file_path.exists():
                    logger.warning(f"File not found, aborting upload: {file_path}")
                    all_files_uploaded = False
                    break
                try:
                    logger.info(f"Uploading file: {file_path}")
                    # Infer mime type and default to text/plain if unknown
                    mime_type, _ = mimetypes.guess_type(file_path)
                    if mime_type is None:
                        mime_type = "text/plain"
                        logger.info(
                            f"Could not determine mime type for {file_path}. Defaulting to {mime_type}."
                        )

                    uploaded_file = self.client.files.upload(
                        file=file_path_str, mime_type=mime_type
                    )
                    uploaded_files.append(uploaded_file)
                except Exception as e:
                    logger.warning(
                        f"Failed to upload file {file_path}: {e}. Aborting upload."
                    )
                    all_files_uploaded = False
                    break

            if all_files_uploaded:
                upload_successful = True

        if upload_successful:
            logger.info(f"Successfully uploaded {len(uploaded_files)} files.")
            prompt_text = Prompts.REVIEW_PROMPT_WITH_FILES.format(diff=diff, context="")
            prompt_parts.append(prompt_text)
            prompt_parts.extend(uploaded_files)
        else:
            if self.uploads_enabled and file_paths:
                logger.warning(
                    "File uploads enabled, but no files were successfully uploaded. Falling back to in-prompt context."
                )

            context = ""
            if file_paths and parsed_files:
                logger.info("Building file context for prompt...")
                context_parts = []
                temp_path_map = {Path(p).name: p for p in file_paths}

                for pf in parsed_files:
                    temp_path_str = temp_path_map.get(Path(pf.file_path).name)
                    if temp_path_str:
                        lined_content = self._get_line_numbered_content(temp_path_str)
                        if lined_content:
                            context_parts.append(
                                f"--- FILE: {pf.file_path} ---\n{lined_content}\n--- END FILE: {pf.file_path} ---"
                            )
                context = "\n".join(context_parts)

            # The updated prompt handles both diff and context, so we can pass the diff
            # to provide nuance and developer intent.
            prompt_text = Prompts.REVIEW_PROMPT.format(diff=diff, context=context)
            prompt_parts.append(prompt_text)

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
                contents=prompt_parts,
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

    def _get_line_numbered_content(self, file_path: str) -> Optional[str]:
        """Reads a file and returns its content with prepended line numbers."""
        try:
            with open(file_path, "r") as f:
                content = f.read()
            return "\n".join(
                f"{i+1}: {line}" for i, line in enumerate(content.splitlines())
            )
        except FileNotFoundError:
            logger.warning(f"Could not find temp file for context: {file_path}")
            return None
        except Exception as e:
            logger.error(f"Failed to read and number file {file_path}: {e}")
            return None

    def generate_summary(
        self, suggestions: List[CodeSuggestion], as_text: bool = False
    ) -> Union[CodeReviewSummary, str]:
        if not suggestions:
            summary = CodeReviewSummary(
                overview="Great work! I have no suggestions for improvement.",
                key_improvements=[],
                minor_suggestions=[],
                critical_issues=[],
            )
            return summary.overview if as_text else summary

        suggestions_text = ""
        for s in suggestions:
            suggestions_text += f"- **File:** `{s.file_name}` (Line: {s.start_line})\n"
            suggestions_text += f"  - **Comment:** {s.comment}\n"

        prompt = Prompts.SUMMARIZE_REVIEW_PROMPT.format(suggestions=suggestions_text)

        if as_text:
            response = self.client.models.generate_content(
                model=f"models/{self.model_name}", contents=[prompt]
            )
            return response.text

        config = genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CodeReviewSummary,
        )
        response = self.client.models.generate_content(
            model=f"models/{self.model_name}",
            contents=[prompt],
            config=config,
        )
        return response.parsed

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

    def is_summary_different(self, summary_a: str, summary_b: str) -> bool:
        """Compares two summaries to see if they are semantically different."""
        prompt = Prompts.COMPARE_SUMMARIES_PROMPT.format(
            summary_a=summary_a, summary_b=summary_b
        )
        try:
            logger.info("Comparing summaries for semantic differences...")
            response = self.client.models.generate_content(
                model=f"models/{self.model_name}", contents=[prompt]
            )

            verdict = response.text.strip().upper()
            logger.info(f"Summary comparison verdict: {verdict}")

            return verdict == "DIFFERENT"
        except Exception as e:
            logger.error(f"An error occurred during summary comparison: {e}")
            # Default to assuming they are different to be safe and force an update.
            return True
