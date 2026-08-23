from unittest.mock import MagicMock

from src.guards.base import GuardAction
from src.guards.duplicate_approval import DuplicateApprovalGuard
from src.models.code_review import CodeReview, Verdict
from src.models.repository import Repository
from src.models.pull_request import PullRequest


def _make_review(verdict: Verdict) -> CodeReview:
    return CodeReview(verdict=verdict, code_suggestions=[])


def _make_context(verdict: Verdict, has_existing: bool = False):
    repo = Repository(owner="owner", name="repo")
    pr = PullRequest(number=1)
    review = _make_review(verdict)
    provider = MagicMock()
    provider.has_existing_bot_approval.return_value = has_existing
    return repo, pr, review, provider


def test_allows_non_approve_verdict():
    repo, pr, review, provider = _make_context(Verdict.COMMENT)
    result = DuplicateApprovalGuard().check(repo, pr, review, provider)

    assert result.action == GuardAction.ALLOW
    assert result.review is None
    provider.has_existing_bot_approval.assert_not_called()


def test_downgrades_duplicate_approve():
    repo, pr, review, provider = _make_context(Verdict.APPROVE, has_existing=True)
    result = DuplicateApprovalGuard().check(repo, pr, review, provider)

    assert result.action == GuardAction.ALLOW
    assert result.review is not None
    assert result.review.verdict == Verdict.COMMENT


def test_allows_first_approve():
    repo, pr, review, provider = _make_context(Verdict.APPROVE, has_existing=False)
    result = DuplicateApprovalGuard().check(repo, pr, review, provider)

    assert result.action == GuardAction.ALLOW
    assert result.review is None


def test_allows_request_changes_even_with_existing_approval():
    repo, pr, review, provider = _make_context(
        Verdict.REQUEST_CHANGES, has_existing=True
    )
    result = DuplicateApprovalGuard().check(repo, pr, review, provider)

    assert result.action == GuardAction.ALLOW
    assert result.review is None
    provider.has_existing_bot_approval.assert_not_called()
