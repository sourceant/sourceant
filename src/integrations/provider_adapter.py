from src.models.code_review import CodeReview
from src.models.repository import Repository
from src.models.pull_request import PullRequest


class ProviderAdapter:
    def post_review(
        self, repository: Repository, pull_request: PullRequest, code_review: CodeReview
    ):
        """Abstract method to post a review on the repository."""
        raise NotImplementedError("Subclasses must implement post_review method")

    def has_existing_bot_approval(self, owner: str, repo: str, pr_number: int) -> bool:
        return False
