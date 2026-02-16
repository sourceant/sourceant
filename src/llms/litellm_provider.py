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
    ):
        self.model = model
        self._token_limit = token_limit

    @property
    def token_limit(self) -> int:
        return self._token_limit

    def count_tokens(self, text: str) -> int:
        return litellm.token_counter(model=self.model, text=text)

    @staticmethod
    def format_pr_metadata(pr_metadata: Optional[dict]) -> str:
        if not pr_metadata:
            return "No PR metadata available."

        parts = []
        if pr_metadata.get("title"):
            parts.append(f"**Title:** {pr_metadata['title']}")
        if pr_metadata.get("number"):
            parts.append(f"**PR #:** {pr_metadata['number']}")
        if pr_metadata.get("description"):
            parts.append(f"**Description:** {pr_metadata['description']}")
        if pr_metadata.get("base_ref") or pr_metadata.get("head_ref"):
            base = pr_metadata.get("base_ref", "unknown")
            head = pr_metadata.get("head_ref", "unknown")
            parts.append(f"**Branches:** {head} → {base}")

        return "\n".join(parts) if parts else "No PR metadata available."

    @staticmethod
    def _build_decoupled_diff(parsed_files: List[ParsedDiff]) -> str:
        parts = []
        for pf in parsed_files:
            parts.append(pf.to_decoupled_format())
        return "\n\n".join(parts)

    @staticmethod
    def _format_existing_comments(existing_comments: Optional[list]) -> str:
        if not existing_comments:
            return ""

        parts = [
            "## Existing Review Comments (DO NOT REPEAT)\n"
            "The following comments have already been posted on this PR. "
            "Do NOT generate suggestions that duplicate these — "
            "skip any suggestion that covers the same file, line range, and issue.\n"
        ]
        for c in existing_comments:
            path = c.get("path", "unknown")
            line = c.get("line", "?")
            start = c.get("start_line")
            body = c.get("body", "")
            if start and start != line:
                parts.append(f"- **{path}** (lines {start}-{line}): {body}")
            else:
                parts.append(f"- **{path}** (line {line}): {body}")

        return "\n".join(parts) + "\n"

    def generate_code_review(
        self,
        diff: str,
        parsed_files: Optional[List[ParsedDiff]] = None,
        pr_metadata: Optional[dict] = None,
        existing_comments: Optional[list] = None,
    ) -> Optional[CodeReview]:
        decoupled_diff = diff
        if parsed_files:
            decoupled_diff = self._build_decoupled_diff(parsed_files)

        metadata_str = self.format_pr_metadata(pr_metadata)
        existing_comments_str = self._format_existing_comments(existing_comments)

        user_text = Prompts.REVIEW_PROMPT.format(
            diff=decoupled_diff,
            pr_metadata=metadata_str,
            existing_comments=existing_comments_str,
        )

        try:
            logger.info(f"Generating code review from model: {self.model}...")
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": Prompts.REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
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
