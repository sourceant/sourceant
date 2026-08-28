import asyncio
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine

from src.core.requirements import (
    CODE,
    Requirement,
    RequirementLink,
    RequirementsReader,
    SQLRequirementsRepository,
)
from src.core.scope import Scope
from src.core.services import ServiceRegistry
from src.models.code_review import CodeReview, Verdict
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

_DIFF = """diff --git a/test.py b/test.py
index 1234567..89abcde 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
-def load(path):
+def load(path, retries=3):
     return builtin_list()
"""

SCOPE = Scope.from_mapping(
    {"repository": "test_owner/test_repo", "revision": "head_sha_def"}
)


@pytest.fixture
def plugin():
    return CodeReviewerPlugin()


@pytest.fixture
def repository():
    return Repository(owner="test_owner", name="test_repo")


@pytest.fixture
def pull_request():
    return PullRequest(
        number=1,
        title="Add retries",
        body="",
        head_sha="head_sha_def",
        base_sha="base_sha_abc",
        url="https://github.com/test_owner/test_repo/pull/1",
    )


def _requirements(tmp_path, *, linked_path="test.py", tested=False):
    engine = create_engine(f"sqlite:///{tmp_path / 'requirements.db'}")
    store = SQLRequirementsRepository(engine, create_schema=True)
    store.put(
        SCOPE,
        Requirement(
            id="r1",
            kind="requirement",
            status="open",
            summary="Loading retries on a transient failure",
        ),
    )
    store.put_link(
        SCOPE,
        RequirementLink(
            id="l1", requirement_id="r1", target_kind=CODE, target_id=linked_path
        ),
    )
    return store


def _run(plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha):
    mock_get_sha.return_value = None
    mock_github = MagicMock()
    mock_github_cls.return_value = mock_github
    mock_github.get_diff.return_value = _DIFF
    mock_github.get_existing_bot_review_comments.return_value = []
    mock_github.get_file_content.side_effect = (
        lambda owner, repo, path, sha: "def load(path, retries=3):\n    return 1\n"
    )

    instance = MagicMock()
    mock_llm.return_value = instance
    instance.count_tokens.return_value = 100
    instance.token_limit = 1000000
    instance.generate_code_review.return_value = CodeReview(
        verdict=Verdict.COMMENT, code_suggestions=[]
    )

    result = asyncio.get_event_loop().run_until_complete(
        plugin.generate_review(
            repository,
            pull_request,
            repository_full_name="test_owner/test_repo",
            post=False,
        )
    )
    return result, instance


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_requirement_on_a_changed_file_reaches_the_review(
    mock_llm,
    mock_github_cls,
    mock_value_of,
    mock_get_sha,
    mock_save_record,
    plugin,
    repository,
    pull_request,
    tmp_path,
):
    services = ServiceRegistry()
    services.register(RequirementsReader, _requirements(tmp_path), "test")
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    section = instance.generate_code_review.call_args.kwargs["requirements"]
    assert "Loading retries on a transient failure" in section
    assert "no linked test" in section


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_requirement_on_an_untouched_file_stays_out_of_the_review(
    mock_llm,
    mock_github_cls,
    mock_value_of,
    mock_get_sha,
    mock_save_record,
    plugin,
    repository,
    pull_request,
    tmp_path,
):
    services = ServiceRegistry()
    services.register(
        RequirementsReader, _requirements(tmp_path, linked_path="other.py"), "test"
    )
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    assert instance.generate_code_review.call_args.kwargs["requirements"] is None


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_review_still_runs_when_the_requirement_tables_are_missing(
    mock_llm,
    mock_github_cls,
    mock_value_of,
    mock_get_sha,
    mock_save_record,
    plugin,
    repository,
    pull_request,
    tmp_path,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    services = ServiceRegistry()
    services.register(RequirementsReader, SQLRequirementsRepository(engine), "test")
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    assert instance.generate_code_review.call_args.kwargs["requirements"] is None
