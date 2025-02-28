import jwt
import time
import requests
import os
from ..provider_adapter import ProviderAdapter
from src.models.code_review import CodeReview, Verdict
from src.models.repository import Repository
from src.models.pull_request import PullRequest
from src.utils.logger import logger


class GitHub(ProviderAdapter):
    def __init__(self):
        if (
            not os.getenv("GITHUB_APP_ID")
            or not os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
            or not os.getenv("GITHUB_APP_CLIENT_ID")
        ):
            logger.error("GitHub environment variables not set.")
            raise ValueError("GitHub environment variables not set.")
        self.app_id = os.getenv("GITHUB_APP_ID")
        self.app_private_key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
        self.app_client_id = os.getenv("GITHUB_APP_CLIENT_ID")

    def generate_jwt(self):
        with open(self.app_private_key_path, "r") as f:
            private_key = f.read()
        payload = {
            "iat": int(time.time()),
            "exp": int(time.time()) + (10 * 60),
            "iss": self.app_client_id,
        }
        return jwt.encode(payload, private_key, algorithm="RS256")

    # Figure this out
    def get_installation_id(self, owner: str, repo: str):
        jwt_token = self.generate_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/installation", headers=headers
        )
        response.raise_for_status()
        return response.json()["id"]

    def get_installation_access_token(self, owner: str, repo: str):
        # installation_id = self.get_installation_id(owner, repo)
        installation_id = 61839800  # Hardcoded for now
        jwt_token = self.generate_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        response = requests.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()["token"]

    def post_review(
        self, repository: Repository, pull_request: PullRequest, code_review: CodeReview
    ):
        logger.info(f"Posting review to GitHub for PR #{pull_request.number}")
        if not code_review:
            logger.error("No review data provided.")
            return {
                "status": "error",
                "message": "No review data provided.",
            }
        try:
            access_token = self.get_installation_access_token(
                repository.owner, repository.name
            )
            logger.info(f"Access token: {access_token}")
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            review_data = {
                "body": code_review.summary or "Code Review",
                "event": (
                    code_review.verdict.value
                    if code_review.verdict
                    else Verdict.COMMENTED.value
                ),
                "comments": [],
            }

            if code_review.code_suggestions:
                for suggestion in code_review.code_suggestions:
                    suggestion_body = suggestion.comment
                    if suggestion.suggested_code:
                        suggestion_body += (
                            f"\n\n```suggest\n{suggestion.suggested_code}\n```"
                        )
                    comment = {
                        "path": suggestion.file_name,
                        "body": suggestion_body,
                    }
                    if suggestion.position is not None:
                        comment["position"] = suggestion.position
                    if suggestion.side is not None:
                        comment["side"] = suggestion.side.value
                    if suggestion.start_line is not None:
                        comment["start_line"] = suggestion.start_line
                    if suggestion.line is not None:
                        comment["line"] = suggestion.line
                    review_data["comments"].append(comment)

            logger.info(f"Sending review data for posting : {review_data}")
            url = f"https://api.github.com/repos/{repository.owner}/{repository.name}/pulls/{pull_request.number}/reviews"
            response = requests.post(url, headers=headers, json=review_data)
            response.raise_for_status()
            return {
                "status": "success",
                "message": f"Review posted to GitHub for PR #{pull_request.number}",
                "review_data": code_review.model_dump(),
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Error posting review to GitHub: {e}")
            logger.error(f"Response: {e.response.text}")
            return {"status": "error", "message": f"Error posting review: {e}"}
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            return {"status": "error", "message": f"An unexpected error occurred: {e}"}
