import json

import pytest
from unittest.mock import patch, MagicMock

from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin
from src.models.code_review import (
    CodeReview,
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
    pr.draft = False
    pr.merged = False
    pr.base_sha = "base_sha_abc"
    pr.head_sha = "head_sha_def"
    return pr


class TestIncrementalReview:
    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.model_for")
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
        mock_github.get_diff_between_shas.return_value = _DIFF
        mock_github.get_diff.return_value = _DIFF

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
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
        mock_github.get_diff.assert_not_called()
        assert result["status"] == "success"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.model_for")
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

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
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
    @patch("src.plugins.builtin.code_reviewer.plugin.model_for")
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

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
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
    @patch("src.plugins.builtin.code_reviewer.plugin.model_for")
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

        review = CodeReview(
            verdict=Verdict.APPROVE,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}

        import asyncio

        asyncio.get_event_loop().run_until_complete(
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
    @patch("src.plugins.builtin.code_reviewer.plugin.model_for")
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

        result = asyncio.get_event_loop().run_until_complete(
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
    @patch("src.plugins.builtin.code_reviewer.plugin.model_for")
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

        result = asyncio.get_event_loop().run_until_complete(
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
    @patch("src.plugins.builtin.code_reviewer.plugin.model_for")
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
        mock_llm_instance.generate_code_review.return_value = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            plugin.generate_review(
                repository,
                pull_request,
                repository_full_name="test_owner/test_repo",
                post=False,
            )
        )

        assert result["status"] == "success"
        context = json.loads(
            mock_llm_instance.generate_code_review.call_args.kwargs["code_context"]
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

    from src.core.model.settings import Choice, SettingsModelSource
    from src.models.model_usage import ModelUsageRecord

    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    SQLModel.metadata.create_all(engine, tables=[ModelUsageRecord.__table__])

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
            SettingsModelSource,
            "choice_for",
            return_value=Choice(name="gemini/gemini-2.5-flash", token_limit=1_000_000),
        ),
        patch("litellm.completion", return_value=answered),
        patch("src.core.usage.sql.get_engine", return_value=engine),
    ):
        result = asyncio.get_event_loop().run_until_complete(
            plugin.generate_review(
                repository,
                pull_request,
                event_type="pull_request.opened",
                repository_full_name="test_owner/test_repo",
            )
        )

    assert result["status"] == "success"

    with Session(engine) as session:
        kept = session.exec(select(ModelUsageRecord)).all()

    assert len(kept) == 1
    assert kept[0].purpose == "review"
    assert (kept[0].owner_type, kept[0].owner_id) == (
        "repository",
        "test_owner/test_repo",
    )
    assert (kept[0].input_tokens, kept[0].output_tokens) == (900, 120)
    assert kept[0].cost_micro == 1_200
    assert kept[0].provider == "gemini"
