from pathlib import Path
from typing import Optional, List, Union

import litellm

from src.llms.llm_interface import LLMInterface
from src.prompts.prompts import Prompts
from src.utils.diff_parser import ParsedDiff
from src.utils.logger import logger
from src.models.code_review import (
    CodeReview,
    Verdict,
    CodeSuggestion,
    CodeReviewSummary,
)


class LiteLLMProvider(LLMInterface):
    def __init__(
        self,
        model: str,
        token_limit: int,
        uploads_enabled: bool = False,
    ):
        self.model = model
        self._token_limit = token_limit
        self._uploads_enabled = uploads_enabled

    @property
    def token_limit(self) -> int:
        return self._token_limit

    @property
    def uploads_enabled(self) -> bool:
        return self._uploads_enabled

    def count_tokens(self, text: str) -> int:
        return litellm.token_counter(model=self.model, text=text)

    def generate_code_review(
        self,
        diff: str,
        parsed_files: Optional[List[ParsedDiff]] = None,
        file_paths: Optional[List[str]] = None,
    ) -> Optional[CodeReview]:
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

        if context:
            prompt_text = Prompts.REVIEW_PROMPT_WITH_FILES.format(
                diff=diff, context=context
            )
        else:
            prompt_text = Prompts.REVIEW_PROMPT.format(diff=diff, context=context)

        try:
            logger.info(f"Generating code review from model: {self.model}...")
            response = litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt_text}],
                response_format=CodeReview,
            )

            logger.info("Code review generated successfully.")

            review = CodeReview.model_validate_json(response.choices[0].message.content)

            return review
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while generating code review: {e}"
            )
            return None

    def _get_line_numbered_content(self, file_path: str) -> Optional[str]:
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
            response = litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content

        response = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=CodeReviewSummary,
        )
        return CodeReviewSummary.model_validate_json(
            response.choices[0].message.content
        )

    def generate_text(self, prompt: str) -> str:
        try:
            response = litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"An error occurred during text generation: {e}")
            return ""

    def is_summary_different(self, summary_a: str, summary_b: str) -> bool:
        prompt = Prompts.COMPARE_SUMMARIES_PROMPT.format(
            summary_a=summary_a, summary_b=summary_b
        )
        try:
            logger.info("Comparing summaries for semantic differences...")
            response = litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )

            verdict = response.choices[0].message.content.strip().upper()
            logger.info(f"Summary comparison verdict: {verdict}")

            return verdict == "DIFFERENT"
        except Exception as e:
            logger.error(f"An error occurred during summary comparison: {e}")
            return True
