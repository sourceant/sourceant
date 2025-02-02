from .base import RepositoryAdapter


class GitLabAdapter(RepositoryAdapter):
    def post_review(self, number: int, review_data: dict):
        return {
            "status": "success",
            "message": f"Review posted to GitLab for PR #{number}",
            "review_data": review_data,
        }
