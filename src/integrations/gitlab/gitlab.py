from ..provider_adapter import ProviderAdapter
from src.models.code_review import CodeReview
from src.models.repository import Repository
from src.models.pull_request import PullRequest


class GitLab(ProviderAdapter):
    def post_review(
        self, repository: Repository, pull_request: PullRequest, code_review: CodeReview
    ):
        return {
            "status": "success",
            "message": f"Review posted to GitLab repo : {repository.full_name} for PR #{pull_request.number}",
            "review_data": code_review,
        }
