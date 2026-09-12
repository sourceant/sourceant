from typing import Optional, List, Union

import litellm

from src.llms.errors import LLMError
from src.llms.llm_interface import LLMInterface
from src.prompts.prompts import Prompts
from src.utils.diff_parser import ParsedDiff
from src.utils.logger import logger
from src.models.code_review import (
    CodeReview,
    CodeReviewFindings,
    CodeSuggestion,
    CodeReviewSummary,
    CodeReviewOverview,
    summary_from,
)


class LiteLLMProvider(LLMInterface):
    def __init__(
        self,
        model: str,
        token_limit: int,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        attribution: Optional[dict] = None,
    ):
        self.model = model
        self._token_limit = token_limit
        # Whose key and whose endpoint. Absent, litellm reads the environment,
        # which is how a deployment has always configured this.
        self._api_key = api_key or None
        self._api_base = api_base or None
        # Who the call is on behalf of. Known where the model was chosen and
        # nowhere after it, so it is carried rather than looked up again.
        self._attribution = attribution or {}

    def _spent(self, response, purpose: str) -> None:
        """Keep what the provider says the call consumed.

        The counts come back on the answer and were thrown away with it. A count
        taken from the prompt here would see neither the system prompt nor the
        answer, and the answer is the expensive half.
        """
        from src.core.usage import record_completion

        record_completion(
            response, model=self.model, purpose=purpose, **self._attribution
        )

    def _credentials(self) -> dict:
        given = {}
        if self._api_key:
            given["api_key"] = self._api_key
        if self._api_base:
            given["api_base"] = self._api_base
        return given

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
    def _format_previous_summary(previous_summary: Optional[str]) -> str:
        """What this reviewer already said about the whole change.

        A pass is given only what changed since the one before it, so its own
        position on the change reaches it from here or not at all.
        """
        if not previous_summary:
            return ""
        return (
            "## What You Already Said About This Pull Request\n"
            "You wrote the summary below on an earlier pass. Do not contradict "
            "it. If a change was made because you asked for it, say so rather "
            "than asking for it to be undone. Raise something only if it is "
            "still true of the code in front of you now.\n\n"
            f"{previous_summary}\n\n"
        )

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
        previous_summary: Optional[str] = None,
        code_context: Optional[str] = None,
        requirements: Optional[str] = None,
        knowledge: Optional[str] = None,
        impact: Optional[str] = None,
    ) -> Optional[CodeReview]:
        decoupled_diff = diff
        if parsed_files:
            decoupled_diff = self._build_decoupled_diff(parsed_files)

        metadata_str = self.format_pr_metadata(pr_metadata)
        existing_comments_str = self._format_existing_comments(existing_comments)
        previous_summary_str = self._format_previous_summary(previous_summary)

        user_text = Prompts.REVIEW_PROMPT.format(
            diff=decoupled_diff,
            pr_metadata=metadata_str,
            existing_comments=existing_comments_str,
            previous_summary=previous_summary_str,
            code_context=code_context or "No structural context is available.",
            requirements=requirements or "",
            knowledge=knowledge or "",
            impact=impact or "",
        )

        try:
            logger.info(f"Generating code review from model: {self.model}...")
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=[
                    {"role": "system", "content": Prompts.REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                response_format=CodeReviewFindings,
            )

            self._spent(response, "review")
            logger.info("Code review generated successfully.")

            review = CodeReview.model_validate_json(response.choices[0].message.content)

            return review
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while generating code review: {e}"
            )
            return None

    @staticmethod
    def _standing_summary(previous_summary: Optional[str]) -> str:
        """What the summary said before this push, so the next one revises it.

        A pass is given only what changed since the one before it, and a summary
        written from that alone describes the newest commit rather than the
        change a reader opens the overview for.
        """
        if not previous_summary:
            return ""
        return (
            "The summary below already stands on this pull request. Revise it to "
            "take account of the suggestions given, keeping what is still true "
            "and dropping what has been addressed. Do not replace it with an "
            "account of the latest push.\n\n"
            f"{previous_summary}\n\n"
        )

    def generate_summary(
        self,
        suggestions: List[CodeSuggestion],
        as_text: bool = False,
        previous_summary: Optional[str] = None,
        change_context: Optional[str] = None,
    ) -> Union[CodeReviewSummary, str]:
        if not suggestions and not change_context:
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
            suggestions_text += f"  - **Category:** {s.category.value if s.category else 'Uncategorized'}\n"
            suggestions_text += f"  - **Comment:** {s.comment}\n"

        prompt = Prompts.SUMMARIZE_REVIEW_PROMPT.format(
            suggestions=suggestions_text,
            previous_summary=self._standing_summary(previous_summary),
            change_context=change_context or "",
        )

        if as_text:
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            self._spent(response, "summary")
            return response.choices[0].message.content

        response = litellm.completion(
            **self._credentials(),
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=CodeReviewOverview,
        )
        self._spent(response, "summary")
        written = CodeReviewOverview.model_validate_json(
            response.choices[0].message.content
        )
        return summary_from(suggestions, written)

    def ask_with_tools(
        self,
        messages: list,
        tools: list,
        *,
        purpose: str = "tools",
        require: bool = False,
    ) -> dict:
        """One round of a conversation the model can answer with a request.

        Returns what it said and what it asked for. The loop belongs to the
        caller, which is the only part that knows when it has enough.

        A provider that cannot take tools raises, and the caller reads that
        as this model being unable to ask rather than as a failed review.
        """
        response = litellm.completion(
            **self._credentials(),
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="required" if require else "auto",
        )
        self._spent(response, purpose)
        answered = response.choices[0].message
        return {
            "content": answered.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                }
                for call in (answered.tool_calls or ())
            ],
        }

    def generate_text(self, prompt: str, *, purpose: str = "text") -> str:
        try:
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            self._spent(response, purpose)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"An error occurred during text generation: {e}")
            raise LLMError.wrapping("Text generation", e) from e

    def is_summary_different(self, summary_a: str, summary_b: str) -> bool:
        prompt = Prompts.COMPARE_SUMMARIES_PROMPT.format(
            summary_a=summary_a, summary_b=summary_b
        )
        try:
            logger.info("Comparing summaries for semantic differences...")
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )

            self._spent(response, "summary-comparison")
            verdict = response.choices[0].message.content.strip().upper()
            logger.info(f"Summary comparison verdict: {verdict}")

            return verdict == "DIFFERENT"
        except Exception as e:
            logger.error(f"An error occurred during summary comparison: {e}")
            return True
