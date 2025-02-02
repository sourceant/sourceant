from .base import RepositoryAdapter


class GitHubAdapter(RepositoryAdapter):
    def post_review(self, number: int, review_data: dict):
        return {
            "status": "success",
            "message": f"Review posted to GitHub for PR #{number}",
            "review_data": review_data,
        }
