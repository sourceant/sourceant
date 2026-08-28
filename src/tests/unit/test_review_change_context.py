import asyncio
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine

from src.core.requirements import (
    CODE,
    Requirement,
    RequirementLink,
    RequirementSelector,
    RequirementsReader,
    SQLRequirementsRepository,
)
from src.core.knowledge import (
    KnowledgeLink,
    KnowledgeObject,
    KnowledgeReader,
    KnowledgeSelector,
    SQLKnowledgeRepository,
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

# Knowledge and requirements are recorded against the repository, not against
# whichever commit happened to be current when somebody wrote them down.
SCOPE = Scope.from_mapping({"repository": "test_owner/test_repo"})
CODE_SCOPE = SCOPE.extend({"revision": "head_sha_def"})


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
    assert "r1 (open)" in section


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


class _IntentSelector:
    """Stands in for a selector that reads intent rather than following links."""

    def __init__(self):
        self.seen = None

    def select(self, selection):
        self.seen = selection
        return (
            Requirement(
                id="r9",
                kind="requirement",
                status="open",
                summary="Nothing links this to the change",
            ),
        )


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_registered_selector_decides_instead_of_the_links(
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
    selector = _IntentSelector()
    services = ServiceRegistry()
    services.register(RequirementsReader, _requirements(tmp_path), "test")
    services.register(RequirementSelector, selector, "test")
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    section = instance.generate_code_review.call_args.kwargs["requirements"]
    assert "Nothing links this to the change" in section
    assert "Loading retries on a transient failure" not in section


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_selector_is_told_what_the_change_is(
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
    selector = _IntentSelector()
    services = ServiceRegistry()
    services.register(RequirementSelector, selector, "test")
    plugin.bind_services(services)

    _run(plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha)

    assert selector.seen.paths == ("test.py",)
    assert selector.seen.title == "Add retries"
    assert selector.seen.scope == SCOPE


def _knowledge(tmp_path, *, linked_path="test.py"):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    store = SQLKnowledgeRepository(engine, create_schema=True)
    store.put(
        SCOPE,
        KnowledgeObject(
            id="d1",
            kind="decision",
            status="approved",
            summary="Retries are capped at three attempts",
        ),
    )
    store.put_link(
        SCOPE,
        KnowledgeLink(
            id="kl1", knowledge_id="d1", target_kind="code", target_id=linked_path
        ),
    )
    return store


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_decision_governing_a_changed_file_reaches_the_review(
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
    services.register(KnowledgeReader, _knowledge(tmp_path), "test")
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    section = instance.generate_code_review.call_args.kwargs["knowledge"]
    assert "Retries are capped at three attempts" in section
    assert "d1 (decision, approved)" in section


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_decision_on_an_untouched_file_stays_out_of_the_review(
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
        KnowledgeReader, _knowledge(tmp_path, linked_path="other.py"), "test"
    )
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    assert instance.generate_code_review.call_args.kwargs["knowledge"] is None


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_store_that_holds_no_links_reviews_without_knowledge(
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
    from src.core.knowledge import InMemoryKnowledgeRepository

    services = ServiceRegistry()
    services.register(KnowledgeReader, InMemoryKnowledgeRepository(), "test")
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    assert instance.generate_code_review.call_args.kwargs["knowledge"] is None


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_registered_knowledge_selector_decides_instead_of_the_links(
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
    class _Selector:
        def select(self, selection):
            return (
                KnowledgeObject(
                    id="d9",
                    kind="constraint",
                    status="approved",
                    summary="Nothing links this to the change",
                ),
            )

    services = ServiceRegistry()
    services.register(KnowledgeReader, _knowledge(tmp_path), "test")
    services.register(KnowledgeSelector, _Selector(), "test")
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    section = instance.generate_code_review.call_args.kwargs["knowledge"]
    assert "Nothing links this to the change" in section
    assert "Retries are capped" not in section


_BINARY_DIFF = """diff --git a/logo.png b/logo.png
index 1234567..89abcde 100644
Binary files a/logo.png and b/logo.png differ
"""


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_a_change_with_nothing_readable_still_reviews(
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

    mock_get_sha.return_value = None
    mock_github = MagicMock()
    mock_github_cls.return_value = mock_github
    mock_github.get_diff.return_value = _BINARY_DIFF
    mock_github.get_existing_bot_review_comments.return_value = []
    mock_github.get_file_content.side_effect = lambda owner, repo, path, sha: None

    instance = MagicMock()
    mock_llm.return_value = instance
    instance.count_tokens.return_value = 10
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

    assert result["status"] == "success"
    assert instance.generate_code_review.call_args.kwargs["requirements"] is None


@patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
@patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
@patch("src.plugins.builtin.code_reviewer.plugin.value_of", return_value=20)
@patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
@patch("src.plugins.builtin.code_reviewer.plugin.llm")
def test_what_a_change_reaches_is_carried_into_the_review(
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
    from src.core.impact import (
        ChangeImpact,
        ChangeImpactResolver,
        ImpactFinding,
    )
    from src.core.topology import TopologySubgraph

    class _Reaches:
        def resolve(self, request):
            return ChangeImpact(
                topology=TopologySubgraph((), (), False),
                compatibility=(),
                findings=(
                    ImpactFinding(
                        id="f1",
                        state="open",
                        summary="The mobile client calls this endpoint",
                        changed_code_ids=("file:test.py",),
                        topology_entity_ids=("system:mobile-client",),
                        compatibility_evidence_id="c1",
                        certain=False,
                    ),
                ),
                truncated=False,
            )

    services = ServiceRegistry()
    services.register(ChangeImpactResolver, _Reaches(), "test")
    plugin.bind_services(services)

    result, instance = _run(
        plugin, repository, pull_request, mock_github_cls, mock_llm, mock_get_sha
    )

    assert result["status"] == "success"
    section = instance.generate_code_review.call_args.kwargs["impact"]
    assert "The mobile client calls this endpoint" in section
    assert "system:mobile-client" in section
    assert "uncertain" in section


def test_code_is_pinned_to_a_commit_and_knowledge_is_not():
    from src.core.change_context import ChangedFile, ChangeSet

    changes = ChangeSet(
        scope=SCOPE, files=(ChangedFile(path="a.py"),), revision="head_sha_def"
    )

    assert changes.scope == SCOPE
    assert changes.code_scope == CODE_SCOPE
    assert changes.code_scope.get("revision") == "head_sha_def"
    assert changes.scope.get("revision") is None


def test_without_a_commit_code_is_filed_with_everything_else():
    from src.core.change_context import ChangedFile, ChangeSet

    changes = ChangeSet(scope=SCOPE, files=(ChangedFile(path="a.py"),))

    assert changes.code_scope == SCOPE
