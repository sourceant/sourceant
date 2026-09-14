from src.prompts.prompts import Prompts
from src.llms.litellm_provider import LiteLLMProvider


class TestReviewSystemPrompt:
    def test_system_prompt_is_non_empty(self):
        assert len(Prompts.REVIEW_SYSTEM_PROMPT) > 100

    def test_system_prompt_contains_reviewer_role(self):
        assert "expert code reviewer" in Prompts.REVIEW_SYSTEM_PROMPT

    def test_system_prompt_contains_review_criteria(self):
        assert "Review Criteria" in Prompts.REVIEW_SYSTEM_PROMPT

    def test_system_prompt_contains_line_number_guidelines(self):
        assert "Line Number Guidelines" in Prompts.REVIEW_SYSTEM_PROMPT

    def test_system_prompt_contains_json_format(self):
        assert "Feedback Format (JSON)" in Prompts.REVIEW_SYSTEM_PROMPT

    def test_system_prompt_contains_code_suggestions_rules(self):
        assert "code_suggestions" in Prompts.REVIEW_SYSTEM_PROMPT

    def test_system_prompt_requires_machine_checkable_structural_claims(self):
        assert '"claims"' in Prompts.REVIEW_SYSTEM_PROMPT
        assert '"predicate": "<IMPORTED|DEFINED>"' in Prompts.REVIEW_SYSTEM_PROMPT
        assert "Do not infer structural facts" in Prompts.REVIEW_SYSTEM_PROMPT


class TestReviewUserPrompts:
    def test_review_prompt_has_placeholders(self):
        assert "{pr_metadata}" in Prompts.REVIEW_SETTLED
        assert "{diff}" in Prompts.REVIEW_CHANGING
        assert "{existing_comments}" in Prompts.REVIEW_CHANGING

    def test_review_prompt_mentions_decoupled_format(self):
        assert "__old hunk__" in Prompts.REVIEW_CHANGING
        assert "__new hunk__" in Prompts.REVIEW_CHANGING

    def test_nothing_that_changes_between_passes_is_in_the_settled_half(self):
        """A varying block early in a prompt costs the cache for all of it."""
        for changes_per_pass in ("{existing_comments}", "{code_context}", "{diff}"):
            assert changes_per_pass not in Prompts.REVIEW_SETTLED

    def test_everything_the_passes_share_is_in_the_settled_half(self):
        for same_every_pass in (
            "{pr_metadata}",
            "{previous_summary}",
            "{requirements}",
            "{knowledge}",
            "{impact}",
            "{related_code}",
        ):
            assert same_every_pass in Prompts.REVIEW_SETTLED


class TestFormatPrMetadata:
    def test_full_metadata(self):
        meta = {
            "title": "Fix login bug",
            "description": "Resolves timeout issue",
            "number": 42,
            "base_ref": "main",
            "head_ref": "fix/login",
        }
        result = LiteLLMProvider.format_pr_metadata(meta)
        assert "**Title:** Fix login bug" in result
        assert "**PR #:** 42" in result
        assert "**Description:** Resolves timeout issue" in result
        assert "**Branches:** fix/login → main" in result

    def test_metadata_without_description(self):
        meta = {
            "title": "Add feature",
            "number": 10,
            "base_ref": "main",
            "head_ref": "feat/new",
        }
        result = LiteLLMProvider.format_pr_metadata(meta)
        assert "**Title:** Add feature" in result
        assert "Description" not in result

    def test_metadata_without_branches(self):
        meta = {
            "title": "Quick fix",
            "number": 5,
        }
        result = LiteLLMProvider.format_pr_metadata(meta)
        assert "**Title:** Quick fix" in result
        assert "Branches" not in result

    def test_none_metadata(self):
        result = LiteLLMProvider.format_pr_metadata(None)
        assert result == "No PR metadata available."

    def test_empty_metadata(self):
        result = LiteLLMProvider.format_pr_metadata({})
        assert result == "No PR metadata available."
