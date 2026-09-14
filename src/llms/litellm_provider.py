import json
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

# Providers set their own floor for what is worth caching, between roughly
# five hundred and four thousand tokens. The highest published floor is used:
# a mark under a provider's own line is refused rather than ignored.
CACHEABLE_FROM = 4096

SECONDS_PER_DAY = 24 * 60 * 60

# What model answers are filed under in the cache.
RESPONSES = "model-response"

# What a provider says when the cache, rather than the request, is what it
# would not accept. Matched on the words because the exception a provider
# raises for it is its own, and there is one of those for every provider.
CACHE_REFUSALS = ("cache", "cached_content", "cachedcontents", "ephemeral")


def _is_a_round(written: str) -> bool:
    """Whether a kept answer still reads as one round of a conversation."""
    try:
        round = json.loads(written)
    except (TypeError, ValueError):
        return False
    return isinstance(round, dict) and "tool_calls" in round


def _about_caching(failure: Exception) -> bool:
    """Whether a failure is the provider refusing to keep part of the prompt.

    Asking again without the mark is worth doing for that and nothing else. A
    bad key, a rate limit or a timeout fails the same way the second time, and
    asking twice doubles what the run costs and what it counts against.
    """
    said = str(failure).lower()
    return any(word in said for word in CACHE_REFUSALS)


class LiteLLMProvider(LLMInterface):
    def __init__(
        self,
        model: str,
        token_limit: int,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        attribution: Optional[dict] = None,
        cache_prompts: bool = True,
    ):
        self.model = model
        self._token_limit = token_limit
        # See _layered: on a provider that keeps a marked prompt in a store of
        # its own, marking is billed where matching a prefix is not.
        self._cache_prompts = cache_prompts
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

    def missing_credentials(self) -> List[str]:
        """What this model needs to authenticate with and cannot find.

        Asked of litellm rather than answered here: which variable holds the
        key is a fact about the provider, and there is one of those for every
        provider litellm routes to. An empty answer is not a promise the key
        works, only that there is one to try. A model litellm does not
        recognise also answers empty, so an unfamiliar name is never mistaken
        for a missing key.
        """
        try:
            answered = litellm.validate_environment(
                model=self.model,
                api_key=self._api_key,
                api_base=self._api_base,
            )
        except Exception:
            logger.warning(
                "Could not read what %s needs to authenticate with",
                self.model,
                exc_info=True,
            )
            return []
        if answered.get("keys_in_environment"):
            return []
        return [str(name) for name in answered.get("missing_keys") or []]

    @property
    def token_limit(self) -> int:
        return self._token_limit

    def count_tokens(self, text: str) -> int:
        return litellm.token_counter(model=self.model, text=text)

    def _caches_prompts(self) -> bool:
        """Whether this model can be told which part of a prompt to keep."""
        try:
            from litellm.utils import supports_prompt_caching

            return bool(supports_prompt_caching(model=self.model))
        except Exception:
            return False

    def _layered(self, settled: str, changing: str) -> Union[str, List[dict]]:
        """The prompt in two parts, the settled one first.

        The order is what matters and it is always kept. Providers cache a
        matching prefix, and the ones that do it on their own need no more
        than for the shared half to come first.

        Marking the boundary is a different thing and is not free everywhere.
        On Gemini a mark moves the call off implicit caching, which happens by
        itself at no charge, and onto a stored cache with its own lifetime and
        its own bill. So the mark is asked for rather than assumed, and is
        skipped for a model that cannot use it or a prompt too short to reach
        any provider's floor.
        """
        if not self._cache_prompts or not self._caches_prompts():
            return settled + changing
        try:
            if self.count_tokens(settled) < CACHEABLE_FROM:
                return settled + changing
        except Exception:
            return settled + changing
        return [
            {
                "type": "text",
                "text": settled,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": changing},
        ]

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

    def _asked_once(
        self, purpose: str, question: str, ask, usable=None
    ) -> Optional[str]:
        """What this model answered, asked for only if it has not answered yet.

        A review asks several times and the expensive part is over long before
        the last of them, so a run that fails near the end has already paid for
        almost all of itself. Keeping each answer against the question that
        produced it means the run after it pays only for what is missing.

        Nothing is recorded as consumed on a reused answer, because nothing
        was: the saving shows in the usage report as calls that did not happen.
        """
        from src.core.cache import cache, keyed
        from src.core.settings import value_of

        repository = str(self._attribution.get("repository") or "")
        try:
            days = int(
                value_of("review.reuse_responses_days", repository=repository or None)
                or 0
            )
        except Exception:
            days = 0
        ttl = days * SECONDS_PER_DAY
        if ttl <= 0:
            return ask()
        # The model and the account are in the key: two models answer the same
        # question differently, and two accounts must never read each other's
        # answers even where they asked the identical thing.
        key = keyed(
            purpose,
            self.model,
            *(
                f"{named}={value}"
                for named, value in sorted(self._attribution.items())
                if value
            ),
            question,
        )
        kept = cache().get(RESPONSES, key)
        if kept is not None:
            if usable is None or usable(kept):
                logger.info(f"Reusing what {self.model} already answered for {purpose}")
                return kept
            # Kept before anything checked it could be read. Left there it
            # would be served until it expired, so the run that finds it is
            # the run that clears it.
            logger.warning(f"Discarding an unreadable {purpose} answer that was kept")
            cache().forget(RESPONSES, key)
        answered = ask()
        if answered and (usable is None or usable(answered)):
            cache().set(RESPONSES, key, answered, ttl=ttl)
        return answered

    def _asked(self, content: Union[str, List[dict]]):
        return litellm.completion(
            **self._credentials(),
            model=self.model,
            messages=[
                {"role": "system", "content": Prompts.REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            response_format=CodeReviewFindings,
        )

    def _read(self, settled: str, changing: str):
        """Ask for a review, and ask again unmarked if the marking was refused.

        A provider that keeps a marked prompt in a store of its own decides for
        itself whether a given prompt is worth storing, and refuses outright
        rather than carrying on uncached. Losing a review over where a cache
        boundary happened to fall costs far more than reading the prompt again.
        """
        layered = self._layered(settled, changing)
        if isinstance(layered, str):
            return self._asked(layered)
        try:
            return self._asked(layered)
        except Exception as refused:
            if not _about_caching(refused):
                raise
            logger.warning(
                "%s would not keep part of this prompt, asking again without: %s",
                self.model,
                refused,
            )
            return self._asked(settled + changing)

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
        related_code: Optional[str] = None,
        analysis: Optional[str] = None,
    ) -> Optional[CodeReview]:
        decoupled_diff = diff
        if parsed_files:
            decoupled_diff = self._build_decoupled_diff(parsed_files)

        metadata_str = self.format_pr_metadata(pr_metadata)
        existing_comments_str = self._format_existing_comments(existing_comments)
        previous_summary_str = self._format_previous_summary(previous_summary)

        settled = Prompts.REVIEW_SETTLED.format(
            pr_metadata=metadata_str,
            previous_summary=previous_summary_str,
            requirements=requirements or "",
            knowledge=knowledge or "",
            impact=impact or "",
            analysis=f"{analysis}\n\n" if analysis else "",
            related_code=f"{related_code}\n\n" if related_code else "",
        )
        changing = Prompts.REVIEW_CHANGING.format(
            diff=decoupled_diff,
            existing_comments=existing_comments_str,
            code_context=code_context or "No structural context is available.",
        )

        def read() -> str:
            response = self._read(settled, changing)
            self._spent(response, "review")
            return response.choices[0].message.content

        def readable(written: str) -> bool:
            try:
                CodeReview.model_validate_json(written)
                return True
            except Exception:
                return False

        try:
            logger.info(f"Generating code review from model: {self.model}...")
            written = self._asked_once(
                "review", settled + changing, read, usable=readable
            )
            if written is None:
                return None
            logger.info("Code review generated successfully.")

            return CodeReview.model_validate_json(written)
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

        def summarize(shape=None) -> str:
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                **({"response_format": shape} if shape else {}),
            )
            self._spent(response, "summary")
            return response.choices[0].message.content

        if as_text:
            return self._asked_once("summary-text", prompt, summarize)

        def an_overview(written: str) -> bool:
            try:
                CodeReviewOverview.model_validate_json(written)
                return True
            except Exception:
                return False

        answered = self._asked_once(
            "summary",
            prompt,
            lambda: summarize(CodeReviewOverview),
            usable=an_overview,
        )
        return summary_from(
            suggestions, CodeReviewOverview.model_validate_json(answered)
        )

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

        Kept like any other answer. A round is decided entirely by what was
        sent, and the loop resends everything before it, so a run repeated
        after a failure asks the same questions in the same order and would
        otherwise pay for every one of them again. What the model asked to
        look at is replayed; the looking itself still happens, because that
        reads the code as it is now rather than as it was.
        """

        def ask() -> str:
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="required" if require else "auto",
            )
            self._spent(response, purpose)
            answered = response.choices[0].message
            return json.dumps(
                {
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
            )

        # The tools and whether one was compulsory decide the answer as much
        # as the conversation does, so all three name the round.
        question = json.dumps(
            {"messages": messages, "tools": tools, "require": require},
            sort_keys=True,
            default=str,
        )
        answered = self._asked_once(purpose, question, ask, usable=_is_a_round)
        try:
            return json.loads(answered)
        except (TypeError, ValueError):
            return {"content": "", "tool_calls": []}

    def generate_text(self, prompt: str, *, purpose: str = "text") -> str:
        def say() -> str:
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            self._spent(response, purpose)
            return response.choices[0].message.content

        try:
            return self._asked_once(purpose, prompt, say)
        except Exception as e:
            logger.error(f"An error occurred during text generation: {e}")
            raise LLMError.wrapping("Text generation", e) from e

    def is_summary_different(self, summary_a: str, summary_b: str) -> bool:
        prompt = Prompts.COMPARE_SUMMARIES_PROMPT.format(
            summary_a=summary_a, summary_b=summary_b
        )

        def compare() -> str:
            response = litellm.completion(
                **self._credentials(),
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            self._spent(response, "summary-comparison")
            return response.choices[0].message.content

        def a_verdict(written: str) -> bool:
            return written.strip().upper() in ("DIFFERENT", "SAME")

        try:
            logger.info("Comparing summaries for semantic differences...")
            verdict = (
                self._asked_once(
                    "summary-comparison", prompt, compare, usable=a_verdict
                )
                .strip()
                .upper()
            )
            logger.info(f"Summary comparison verdict: {verdict}")

            return verdict == "DIFFERENT"
        except Exception as e:
            logger.error(f"An error occurred during summary comparison: {e}")
            return True
