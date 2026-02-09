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


class TestReviewUserPrompts:
    def test_review_prompt_has_placeholders(self):
        assert "{diff}" in Prompts.REVIEW_PROMPT
        assert "{context}" in Prompts.REVIEW_PROMPT
        assert "{pr_metadata}" in Prompts.REVIEW_PROMPT

    def test_review_prompt_with_files_has_placeholders(self):
        assert "{diff}" in Prompts.REVIEW_PROMPT_WITH_FILES
        assert "{context}" in Prompts.REVIEW_PROMPT_WITH_FILES
        assert "{pr_metadata}" in Prompts.REVIEW_PROMPT_WITH_FILES

    def test_review_prompt_mentions_decoupled_format(self):
        assert "__old hunk__" in Prompts.REVIEW_PROMPT
        assert "__new hunk__" in Prompts.REVIEW_PROMPT

    def test_review_prompt_with_files_mentions_decoupled_format(self):
        assert "__old hunk__" in Prompts.REVIEW_PROMPT_WITH_FILES
        assert "__new hunk__" in Prompts.REVIEW_PROMPT_WITH_FILES


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
