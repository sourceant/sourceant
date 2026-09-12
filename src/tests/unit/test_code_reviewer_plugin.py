import json
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock

from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin
from src.models.code_review import (
    CodeReview,
    CodeReviewSummary,
    CodeReviewOverview,
    CodeSuggestion,
    Side,
    SuggestionCategory,
    Verdict,
)
from src.core.review_evidence import ReviewClaim, StructuralPredicate
from src.core.code_index import CodeEdge, CodeIndexReader, CodeNode, InMemoryCodeIndex
from src.core.scope import Scope
from src.core.services import ServiceRegistry
from src.models.repository import Repository
from src.models.pull_request import PullRequest
from src.core.responses import success_response


# Settings are looked up by key, so a fixture has to answer by key. Returning
# one number for every setting made the reading budget whatever the file limit
# happened to be, and a budget of twenty tokens reads any change in parts.
def _setting(key, **_):
    if key == "review.reading_budget":
        return 1_000_000
    return 20


def _one_file(key, **_):
    """A file limit of one, with the budget left alone."""
    if key == "review.reading_budget":
        return 1_000_000
    return 1


@pytest.fixture
def plugin():
    return CodeReviewerPlugin()


@pytest.fixture
def repository():
    return Repository(owner="test_owner", name="test_repo")


@pytest.fixture
def pull_request():
    pr = MagicMock(spec=PullRequest)
    pr.number = 1
    pr.title = "Test PR"
    pr.body = None
    pr.draft = False
    pr.merged = False
    pr.base_sha = "base_sha_abc"
    pr.head_sha = "head_sha_def"
    return pr


class TestIncrementalReview:
    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_synchronize_uses_incremental_diff(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = "prev_sha_123"

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        fixtures = Path(__file__).parents[1] / "fixtures/review-overview"
        latest_diff = (fixtures / "latest.diff").read_text()
        full_diff = (fixtures / "full.diff").read_text()
        mock_github.get_diff_between_shas.return_value = latest_diff
        mock_github.get_diff.return_value = full_diff

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                event_type="pull_request.synchronize",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_github.get_diff_between_shas.assert_called_once_with(
            owner="test_owner",
            repo="test_repo",
            base_sha="prev_sha_123",
            head_sha="head_sha_def",
        )
        mock_github.get_diff.assert_called_once_with(
            owner="test_owner",
            repo="test_repo",
            pr_number=1,
            base_sha="base_sha_abc",
            head_sha="head_sha_def",
        )
        assert (
            mock_llm_instance.generate_code_review.call_args.kwargs["diff"]
            == latest_diff
        )
        overview_prompt = mock_llm_instance.generate_summary.call_args.kwargs[
            "change_context"
        ]
        assert "def suggest_groups" in overview_prompt
        assert "priority" in overview_prompt
        assert result["status"] == "success"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_synchronize_falls_back_on_force_push(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = "old_sha_gone"

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff_between_shas.side_effect = ValueError("Not found")
        mock_github.get_diff.return_value = _DIFF

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                event_type="pull_request.synchronize",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_github.get_diff.assert_called_once()
        assert result["status"] == "success"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_first_review_uses_full_diff(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = _DIFF

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                event_type="pull_request.synchronize",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_github.get_diff_between_shas.assert_not_called()
        mock_github.get_diff.assert_called_once()
        assert result["status"] == "success"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_saves_review_record_on_success(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = _DIFF

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )

        review = CodeReview(
            verdict=Verdict.APPROVE,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                event_type="pull_request.opened",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_save_record.assert_called_once_with(
            "test_owner/test_repo",
            pull_request.number,
            pull_request.head_sha,
            pull_request.base_sha,
        )


def _make_suggestion(
    file_name="test.py",
    start_line=10,
    end_line=12,
    comment="Consider using a context manager here for resource cleanup.",
    suggested_code="with open(path) as f:\n    data = f.read()",
):
    return CodeSuggestion(
        file_name=file_name,
        start_line=start_line,
        end_line=end_line,
        side=Side.RIGHT,
        comment=comment,
        category=SuggestionCategory.STYLE,
        suggested_code=suggested_code,
    )


class TestFilterDuplicateSuggestions:
    def test_removes_duplicate_same_file_overlapping_lines_similar_comment(
        self, plugin
    ):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_removes_duplicate_by_matching_suggested_code(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Rephrased differently.\n\n```suggestion\nwith open(path) as f:\n    data = f.read()\n```",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_removes_duplicate_with_fuzzy_line_shift(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 15,
                "start_line": 13,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_keeps_suggestion_on_different_file(self, plugin):
        existing = [
            {
                "path": "other.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 1

    def test_keeps_suggestion_on_non_overlapping_lines(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 50,
                "start_line": 48,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 1

    def test_keeps_suggestion_with_different_comment_and_code(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Variable naming is inconsistent with project conventions.\n\n```suggestion\nuser_name = get_name()\n```",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 1

    def test_filters_when_existing_body_contains_suggestion_block(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Use context manager.\n\n```suggestion\nwith open(path) as f:\n    data = f.read()\n```",
                "line": 11,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_handles_empty_existing_comments(self, plugin):
        suggestions = [_make_suggestion()]
        result = plugin._filter_duplicate_suggestions(suggestions, [])
        assert len(result) == 1

    def test_handles_single_line_existing_comment(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 11,
                "start_line": None,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_detects_rephrased_duplicate_via_high_word_overlap(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "You should consider using a context manager here to ensure proper resource cleanup.",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_detects_duplicate_with_similar_code_different_whitespace(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Fix.\n\n```suggestion\nwith  open( path )  as  f:\n    data  =  f.read()\n```",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0


_DIFF = """diff --git a/test.py b/test.py
index 1111111..2222222 100644
--- a/test.py
+++ b/test.py
@@ -7,7 +7,7 @@ def load(path):
     if not path:
         return None
 
-    f = open(path)
-    data = f.read()
-    f.close()
+    f = open(path, "r")
+    data = f.read()
+    f.close()
     return data
"""


class TestPreviewResponseIsSerializable:
    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_preview_run_renders_as_json(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = _DIFF
        mock_github.get_existing_bot_review_comments.return_value = []

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )
        mock_llm_instance.generate_code_review.return_value = CodeReview(
            verdict=Verdict.REQUEST_CHANGES,
            code_suggestions=[
                CodeSuggestion(
                    file_name="test.py",
                    start_line=10,
                    end_line=12,
                    side=Side.RIGHT,
                    comment="Consider a context manager so the file is closed on failure.",
                    category=SuggestionCategory.BUG,
                    existing_code='    f = open(path, "r")\n    data = f.read()\n    f.close()',
                    suggested_code="    with open(path) as f:\n        data = f.read()",
                )
            ],
        )

        import asyncio

        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                repository_full_name="test_owner/test_repo",
                post=False,
            )
        )

        assert result["status"] == "success"
        mock_github.post_review.assert_not_called()

        body = json.loads(success_response(result).body)
        suggestion = body["data"]["review"]["code_suggestions"][0]
        assert suggestion["side"] == "RIGHT"
        assert suggestion["category"] == "BUG"
        assert body["data"]["review"]["verdict"] == "REQUEST_CHANGES"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch(
        "src.plugins.builtin.code_reviewer.reviewing.value_of", side_effect=_one_file
    )
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_preview_drops_a_missing_import_claim_disproved_by_post_change_file(
        self,
        mock_llm,
        mock_github_cls,
        mock_value_of,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = None
        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = _DIFF
        mock_github.get_existing_bot_review_comments.return_value = []
        mock_github.get_file_content.return_value = (
            "import logging\n\nlogger = logging.getLogger(__name__)\n"
            "\ndef load(path):\n    return path\n"
        )

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )
        mock_llm_instance.generate_code_review.return_value = CodeReview(
            verdict=Verdict.REQUEST_CHANGES,
            code_suggestions=[
                CodeSuggestion(
                    file_name="test.py",
                    start_line=10,
                    end_line=10,
                    side=Side.RIGHT,
                    comment=(
                        "Missing import logging and logger instantiation will cause "
                        "a NameError."
                    ),
                    category=SuggestionCategory.BUG,
                    existing_code='    f = open(path, "r")',
                    suggested_code="    logging.info(path)",
                    claims=[
                        ReviewClaim(
                            subject="logging",
                            predicate=StructuralPredicate.IMPORTED,
                            expected=False,
                        ),
                        ReviewClaim(
                            subject="logger",
                            predicate=StructuralPredicate.DEFINED,
                            expected=False,
                        ),
                    ],
                )
            ],
        )

        import asyncio

        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                repository_full_name="test_owner/test_repo",
                post=False,
            )
        )

        assert result["status"] == "success"
        assert result["review"]["code_suggestions"] == []
        assert result["review"]["summary"]["critical_issues"] == []
        code_context = mock_llm_instance.generate_code_review.call_args.kwargs[
            "code_context"
        ]
        assert '"file_path":"test.py"' in code_context
        assert '"name":"load"' in code_context
        # That this setting is read, not that it is the only one: a review also
        # asks how much it is worth reading at once.
        mock_value_of.assert_any_call(
            "review.structural_context_file_limit",
            repository="test_owner/test_repo",
        )
        mock_github.get_file_content.assert_called_once_with(
            "test_owner", "test_repo", "test.py", "head_sha_def"
        )

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.reviewing.value_of", side_effect=_setting)
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_review_receives_referenced_definition_source_from_durable_graph(
        self,
        mock_llm,
        mock_github_cls,
        mock_value_of,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        scope = Scope.from_mapping(
            {"repository": "test_owner/test_repo", "revision": "head_sha_def"}
        )
        code = InMemoryCodeIndex()
        changed = CodeNode(
            "file:test.py", frozenset({"File"}), {"file_path": "test.py"}
        )
        definition = CodeNode(
            "file:catalog.py",
            frozenset({"File"}),
            {"file_path": "catalog.py"},
        )
        symbol = CodeNode(
            "symbol:builtin_list", frozenset({"Symbol"}), {"name": "builtin_list"}
        )
        for node in (changed, definition, symbol):
            code.put_node(scope, node)
        code.put_edge(scope, CodeEdge("reference", changed.id, symbol.id, "REFERENCES"))
        code.put_edge(
            scope,
            CodeEdge(
                "definition",
                definition.id,
                symbol.id,
                "DEFINES",
                {"file_path": "catalog.py", "range": [0, 0, 1, 40]},
            ),
        )
        services = ServiceRegistry()
        services.register(CodeIndexReader, code, "test")
        plugin.bind_services(services)

        mock_get_sha.return_value = None
        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = _DIFF
        mock_github.get_existing_bot_review_comments.return_value = []
        contents = {
            "test.py": "def load(path):\n    return builtin_list()\n",
            "catalog.py": (
                "def builtin_list():\n" '    return ["infra/postgres", "welcome"]\n'
            ),
        }
        mock_github.get_file_content.side_effect = (
            lambda owner, repo, path, sha: contents.get(path)
        )

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )
        mock_llm_instance.generate_code_review.return_value = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )

        import asyncio

        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                repository_full_name="test_owner/test_repo",
                post=False,
            )
        )

        assert result["status"] == "success"
        # The graph, then whatever else was found or could not be. Only the
        # first of those is JSON.
        context = json.loads(
            mock_llm_instance.generate_code_review.call_args.kwargs[
                "code_context"
            ].split("\n", 1)[0]
        )
        assert {node["id"] for node in context["nodes"]} >= {
            "file:test.py",
            "file:catalog.py",
        }
        excerpts = {
            excerpt["file_path"]: excerpt for excerpt in context["source_excerpts"]
        }
        assert excerpts["catalog.py"] == {
            "content": (
                "def builtin_list():\n" '    return ["infra/postgres", "welcome"]'
            ),
            "end_line": 2,
            "file_path": "catalog.py",
            "start_line": 1,
        }


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
def test_a_review_records_what_it_spent_against_the_repository(
    mock_github_cls,
    mock_get_sha,
    mock_save_record,
    plugin,
    repository,
    pull_request,
    tmp_path,
):
    """A review is the reason any of this is recorded, so drive one.

    Every other test here replaces the model outright, so nothing has ever
    shown that reviewing a pull request leaves a record, or that the record
    names the repository the review was for.
    """
    import asyncio

    from litellm.types.utils import Choices, Message, ModelResponse, Usage
    from sqlalchemy import create_engine
    from sqlmodel import Session, SQLModel, select

    from src.core.model.settings import LLMConfig, SettingsLLMSource
    from src.models.token_usage import TokenUsageRecord

    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    SQLModel.metadata.create_all(engine, tables=[TokenUsageRecord.__table__])

    mock_get_sha.return_value = None
    mock_github = MagicMock()
    mock_github_cls.return_value = mock_github
    mock_github.get_diff.return_value = _DIFF
    mock_github.post_review.return_value = {"status": "success"}
    mock_github.get_existing_bot_review_comments.return_value = []

    answered = ModelResponse(
        choices=[
            Choices(
                message=Message(
                    content=CodeReview(
                        verdict=Verdict.COMMENT, code_suggestions=[]
                    ).model_dump_json()
                )
            )
        ],
        usage=Usage(prompt_tokens=900, completion_tokens=120),
    )
    answered._hidden_params = {"response_cost": 0.0012}

    with (
        patch.object(
            SettingsLLMSource,
            "config_for",
            return_value=LLMConfig(
                name="gemini/gemini-2.5-flash", token_limit=1_000_000
            ),
        ),
        patch(
            "litellm.completion",
            side_effect=lambda **kwargs: (
                answered.model_copy(
                    update={
                        "choices": [
                            Choices(
                                message=Message(
                                    content=CodeReviewSummary(
                                        overview="The full pull request overview.",
                                        key_improvements=[],
                                        minor_suggestions=[],
                                        critical_issues=[],
                                    ).model_dump_json()
                                )
                            )
                        ]
                    }
                )
                if kwargs.get("response_format") is CodeReviewOverview
                else answered
            ),
        ),
        patch("src.core.usage.sql.get_engine", return_value=engine),
    ):
        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                event_type="pull_request.opened",
                repository_full_name="test_owner/test_repo",
            )
        )

    assert result["status"] == "success"

    with Session(engine) as session:
        kept = session.exec(select(TokenUsageRecord)).all()

    assert len(kept) == 2
    assert {record.purpose for record in kept} == {"review", "summary"}
    assert all(record.owner_id == "test_owner/test_repo" for record in kept)
    assert kept[0].purpose == "review"
    assert (kept[0].owner_type, kept[0].owner_id) == (
        "repository",
        "test_owner/test_repo",
    )
    assert (kept[0].input_tokens, kept[0].output_tokens) == (900, 120)
    assert kept[0].cost_micro == 1_200
    assert kept[0].provider == "gemini"


class TestAReviewSaysWhatItWasAbleToRead:
    """A quiet review and a blind one look identical without this.

    The canned "no suggestions for improvement" summary is produced when a
    review found nothing, whether it read the whole system or none of it.
    """

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def _reviewed(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin=None,
        repository=None,
        pull_request=None,
    ):
        mock_get_sha.return_value = None
        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = _DIFF
        mock_github.get_existing_bot_review_comments.return_value = []
        mock_github.get_file_content.return_value = "def load(path):\n    return 1\n"

        model = MagicMock()
        mock_llm.return_value = model
        model.count_tokens.return_value = 100
        model.token_limit = 1000000
        model.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )
        model.generate_code_review.return_value = CodeReview(
            verdict=Verdict.COMMENT, code_suggestions=[]
        )

        import asyncio

        return asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                repository_full_name="test_owner/test_repo",
                post=False,
            )
        )

    def test_the_result_carries_what_was_read(self, plugin, repository, pull_request):
        result = self._reviewed(
            plugin=plugin, repository=repository, pull_request=pull_request
        )

        assert result["status"] == "success"
        assert "coverage" in result
        assert "attempts" in result["coverage"]

    def test_the_summary_says_it_in_words(self, plugin, repository, pull_request):
        result = self._reviewed(
            plugin=plugin, repository=repository, pull_request=pull_request
        )

        assert result["review"]["summary"]["coverage"].startswith("Read: the diff")

    def test_the_graph_walk_is_recorded_whether_or_not_it_answered(
        self, plugin, repository, pull_request
    ):
        """Asked and unanswered has to be distinguishable from never asked."""
        result = self._reviewed(
            plugin=plugin, repository=repository, pull_request=pull_request
        )

        asked = [
            attempt
            for attempt in result["coverage"]["attempts"]
            if attempt["question"] == "reach"
        ]

        assert asked
        assert {attempt["target"] for attempt in asked} == {"test_owner/test_repo"}
        assert not any(attempt["answered"] for attempt in asked)


class TestASkillReachesTheReviewerAsItsContents:
    """Most skills are a pointer at the page that holds the rule.

    Judged against the pointer, a change is judged against nothing, and the
    model says so, which reads as the change being at fault. The checkout
    path followed those pointers; the pull request path did not.
    """

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.provider_for")
    def test_the_document_a_skill_points_at_is_written_out_under_it(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
        tmp_path,
    ):
        from src.core.skills import Skill, SkillLibrary

        folder = tmp_path / "skills" / "migrations"
        folder.mkdir(parents=True)
        (folder / "rules.md").write_text(
            "Never edit a migration somebody has already run."
        )
        skill = Skill(
            id="migrations",
            name="migrations",
            description="How migrations are written here",
            body="The rules are in rules.md and they are not negotiable.",
            path=str(folder / "SKILL.md"),
            # Stated in by its author, so this holds the inlining to account
            # rather than the wording match that chooses a skill.
            metadata={"sourceant": {"review": True}},
        )

        class _Library:
            def all(self, workspace, repository):
                return (skill,)

        services = ServiceRegistry()
        services.register(SkillLibrary, _Library(), "test")
        plugin.bind_services(services)

        mock_get_sha.return_value = None
        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = _DIFF
        mock_github.get_existing_bot_review_comments.return_value = []
        mock_github.get_file_content.return_value = "def load(path):\n    return 1\n"

        model = MagicMock()
        mock_llm.return_value = model
        model.count_tokens.return_value = 100
        model.token_limit = 1000000
        model.generate_summary.return_value = CodeReviewSummary(
            overview="The full pull request overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )
        model.generate_code_review.return_value = CodeReview(
            verdict=Verdict.COMMENT, code_suggestions=[]
        )

        import asyncio

        asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                repository_full_name="test_owner/test_repo",
                post=False,
            )
        )

        told = model.generate_code_review.call_args.kwargs["knowledge"]
        assert "Never edit a migration somebody has already run." in told


class TestWhetherASkillWasHonoured:
    """A skill attached to a prompt and a skill a review took notice of look
    identical from the outside, and only one of them is worth having."""

    def reviewed(self, plugin, repository, pull_request, checking):
        from src.core.skills import Skill, SkillLibrary, SkillVerdict

        skill = Skill(
            id="migrations",
            name="migrations",
            description="How migrations are written here",
            body="Never edit a migration somebody has already run.",
            metadata={"sourceant": {"review": True}},
        )

        class _Library:
            def all(self, workspace, repository):
                return (skill,)

        services = ServiceRegistry()
        services.register(SkillLibrary, _Library(), "test")
        plugin.bind_services(services)

        asked = []

        class _Checker:
            def __init__(self, ask, model):
                pass

            def check(self, skill, subject):
                asked.append(skill.id)
                return SkillVerdict(skill.id, passed=False, note="Never mentioned it")

        with (
            patch("src.plugins.builtin.code_reviewer.plugin.save_review_record"),
            patch(
                "src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha"
            ) as sha,
            patch("src.plugins.builtin.code_reviewer.plugin.GitHub") as github_cls,
            patch("src.plugins.builtin.code_reviewer.plugin.provider_for") as provider,
            patch(
                "src.plugins.builtin.code_reviewer.reviewing.LLMSkillChecker", _Checker
            ),
            patch(
                "src.plugins.builtin.code_reviewer.reviewing.value_of",
                side_effect=lambda key, **_: (
                    checking if key == "review.check_skills_were_applied" else None
                ),
            ),
        ):
            sha.return_value = None
            github = MagicMock()
            github_cls.return_value = github
            github.get_diff.return_value = _DIFF
            github.get_existing_bot_review_comments.return_value = []
            github.get_file_content.return_value = "def load(path):\n    return 1\n"

            model = MagicMock()
            provider.return_value = model
            model.count_tokens.return_value = 100
            model.token_limit = 1000000
            model.generate_summary.return_value = CodeReviewSummary(
                overview="The full pull request overview.",
                key_improvements=[],
                minor_suggestions=[],
                critical_issues=[],
            )
            model.generate_code_review.return_value = CodeReview(
                verdict=Verdict.COMMENT, code_suggestions=[]
            )

            import asyncio

            result = asyncio.run(
                plugin.generate_review(
                    repository,
                    pull_request,
                    repository_full_name="test_owner/test_repo",
                    post=False,
                )
            )
        return result, asked

    def honoured(self, result):
        return [
            attempt
            for attempt in result["coverage"]["attempts"]
            if attempt["method"] == "honoured"
        ]

    def test_a_skill_the_review_ignored_is_recorded_as_ignored(
        self, plugin, repository, pull_request
    ):
        result, asked = self.reviewed(plugin, repository, pull_request, True)

        assert asked == ["migrations"]
        assert self.honoured(result) == [
            {
                "question": "skills",
                "method": "honoured",
                "answered": False,
                "target": "migrations",
                "reason": "Never mentioned it",
            }
        ]

    def test_turning_it_off_says_so_rather_than_saying_nothing(
        self, plugin, repository, pull_request
    ):
        """Not checked and checked-and-fine have to stay distinguishable."""
        result, asked = self.reviewed(plugin, repository, pull_request, False)

        assert asked == []
        assert self.honoured(result)[0]["reason"] == "not checked"

    def test_it_is_asked_unless_somebody_turns_it_off(self):
        """The point of writing a skill is that a review is held to it."""
        from src.core.settings.definitions import get

        assert get("review.check_skills_were_applied").default is True

    def test_a_check_that_fails_does_not_take_the_review_with_it(
        self, plugin, repository, pull_request
    ):
        from src.core.skills import Skill, SkillLibrary

        class _Raises:
            def __init__(self, ask, model):
                pass

            def check(self, skill, subject):
                raise RuntimeError("the provider timed out")

        skill = Skill(
            id="migrations",
            name="migrations",
            description="How migrations are written here",
            body="Never edit a migration somebody has already run.",
            metadata={"sourceant": {"review": True}},
        )

        class _Library:
            def all(self, workspace, repository):
                return (skill,)

        services = ServiceRegistry()
        services.register(SkillLibrary, _Library(), "test")
        plugin.bind_services(services)

        with (
            patch("src.plugins.builtin.code_reviewer.plugin.save_review_record"),
            patch(
                "src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha"
            ) as sha,
            patch("src.plugins.builtin.code_reviewer.plugin.GitHub") as github_cls,
            patch("src.plugins.builtin.code_reviewer.plugin.provider_for") as provider,
            patch(
                "src.plugins.builtin.code_reviewer.reviewing.LLMSkillChecker", _Raises
            ),
            patch(
                "src.plugins.builtin.code_reviewer.reviewing.value_of",
                side_effect=lambda key, **_: (
                    True if key == "review.check_skills_were_applied" else None
                ),
            ),
        ):
            sha.return_value = None
            github = MagicMock()
            github_cls.return_value = github
            github.get_diff.return_value = _DIFF
            github.get_existing_bot_review_comments.return_value = []
            github.get_file_content.return_value = "def load(path):\n    return 1\n"

            model = MagicMock()
            provider.return_value = model
            model.count_tokens.return_value = 100
            model.token_limit = 1000000
            model.generate_summary.return_value = CodeReviewSummary(
                overview="The full pull request overview.",
                key_improvements=[],
                minor_suggestions=[],
                critical_issues=[],
            )
            model.generate_code_review.return_value = CodeReview(
                verdict=Verdict.COMMENT, code_suggestions=[]
            )

            import asyncio

            result = asyncio.run(
                plugin.generate_review(
                    repository,
                    pull_request,
                    repository_full_name="test_owner/test_repo",
                    post=False,
                )
            )

        assert result["status"] == "success"
        assert "did not finish" in self.honoured(result)[0]["reason"]
