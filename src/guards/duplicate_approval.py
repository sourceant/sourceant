from src.guards.base import GuardAction, GuardResult
from src.models.code_review import CodeReview, Verdict
from src.models.repository import Repository
from src.models.pull_request import PullRequest
from src.integrations.provider_adapter import ProviderAdapter


class DuplicateApprovalGuard:
    def check(
        self,
        repository: Repository,
        pull_request: PullRequest,
        code_review: CodeReview,
        provider: ProviderAdapter,
    ) -> GuardResult:
        if code_review.verdict != Verdict.APPROVE:
            return GuardResult(action=GuardAction.ALLOW)

        if provider.has_existing_bot_approval(
            repository.owner, repository.name, pull_request.number
        ):
            modified = code_review.model_copy(update={"verdict": Verdict.COMMENT})
            return GuardResult(
                action=GuardAction.ALLOW,
                review=modified,
                reason="Downgraded duplicate APPROVE to COMMENT",
            )

        return GuardResult(action=GuardAction.ALLOW)
