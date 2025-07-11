import jwt
import time
import requests
import os
from typing import Dict, Any, Optional
from dateutil.parser import isoparse
from ..provider_adapter import ProviderAdapter
from src.models.code_review import CodeReview, CodeReviewSummary, Verdict
from src.models.repository import Repository
from src.models.pull_request import PullRequest
from src.utils.logger import logger
from src.llms.llm_factory import llm


COMMENT_MARKER = "<!-- SOURCEANT_REVIEW_SUMMARY -->"


class GitHub(ProviderAdapter):
    """GitHub provider implementation for posting code reviews."""

    def __init__(self):
        """Initialize GitHub integration with required environment variables."""
        self.app_id = os.getenv("GITHUB_APP_ID")
        self.app_private_key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
        self.app_client_id = os.getenv("GITHUB_APP_CLIENT_ID")

        if not all([self.app_id, self.app_private_key_path, self.app_client_id]):
            error_msg = (
                "GitHub environment variables not properly configured. "
                "Please set GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH, and GITHUB_APP_CLIENT_ID"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Cache for installation access tokens with expiration
        self._access_tokens: Dict[str, Dict[str, Any]] = {}
        self._app_slug: Optional[str] = None

    def generate_jwt(self) -> str:
        """Generate a JWT token for GitHub App authentication."""
        try:
            with open(self.app_private_key_path, "r") as f:
                private_key = f.read()

            payload = {
                "iat": int(time.time()) - 60,  # 1 minute in the past for clock skew
                "exp": int(time.time()) + (9 * 60),  # 9 minutes from now (max 10)
                "iss": self.app_id,
            }

            return jwt.encode(payload, private_key, algorithm="RS256")

        except FileNotFoundError:
            error_msg = f"Private key file not found at {self.app_private_key_path}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        except Exception as e:
            error_msg = f"Failed to generate JWT token: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def get_installation_id(self, owner: str, repo: str) -> int:
        """Get the installation ID for a GitHub repository."""
        try:
            jwt_token = self.generate_jwt()
            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            response = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/installation",
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            installation_id = response.json().get("id")
            if not installation_id:
                raise ValueError("No installation ID found in response")

            return installation_id

        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to get installation ID for {owner}/{repo}: {str(e)}"
            if hasattr(e, "response") and e.response is not None:
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def get_installation_access_token(self, owner: str, repo: str) -> str:
        """Get an installation access token for a repository, with caching."""
        repo_key = f"{owner}/{repo}"
        logger.info(f"Attempting to get installation access token for {repo_key}")

        # Check cache first
        if repo_key in self._access_tokens:
            token_data = self._access_tokens[repo_key]
            if time.time() < token_data["expires_at"] - 300:
                logger.info(
                    f"Using cached token for {repo_key}. Expires at: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(token_data['expires_at']))}"
                )
                return token_data["token"]
            else:
                logger.info(
                    f"Cached token for {repo_key} has expired. Fetching a new one."
                )
        else:
            logger.info(f"No cached token found for {repo_key}. Fetching a new one.")

        try:
            installation_id = self.get_installation_id(owner, repo)
            jwt_token = self.generate_jwt()
            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            logger.info(f"Requesting new installation access token for {repo_key}")
            response = requests.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            response_data = response.json()
            new_token = response_data.get("token")
            expires_at_str = response_data.get("expires_at")

            if not new_token or not expires_at_str:
                raise ValueError("Token or expiration not found in response")

            new_expires_at = isoparse(expires_at_str).timestamp()

            logger.info(f"Successfully fetched new token for {repo_key}. Caching it.")
            self._access_tokens[repo_key] = {
                "token": new_token,
                "expires_at": new_expires_at,
            }

            return new_token

        except requests.exceptions.RequestException as e:
            error_msg = (
                f"Failed to get installation access token for {owner}/{repo}: {str(e)}"
            )
            if hasattr(e, "response") and e.response is not None:
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def get_app_slug(self) -> str:
        """Get the slug of the GitHub App."""
        if self._app_slug:
            return self._app_slug

        try:
            jwt_token = self.generate_jwt()
            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            response = requests.get(
                "https://api.github.com/app", headers=headers, timeout=30
            )
            response.raise_for_status()

            app_info = response.json()
            slug = app_info.get("slug")
            if not slug:
                raise ValueError("Could not retrieve app slug from GitHub API.")

            self._app_slug = slug
            logger.info(f"Retrieved app slug: {self._app_slug}")
            return self._app_slug

        except (requests.exceptions.RequestException, ValueError) as e:
            error_msg = f"Failed to get app slug: {e}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def _find_overview_comment(
        self, owner: str, repo: str, pr_number: int, headers: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Find the bot's overview comment on a PR."""
        try:
            response = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            comments = response.json()

            for comment in comments:
                if COMMENT_MARKER in comment.get("body", ""):
                    logger.info(
                        f"Found previous overview comment with ID: {comment['id']}"
                    )
                    return comment

            logger.info("No previous overview comment found.")
            return None

        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"Could not search for previous overview comment: {e}")
            return None

    def _create_or_update_overview_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        summary: str,
        headers: Dict[str, str],
    ) -> None:
        """Create or update the main summary comment on a PR."""
        existing_comment = self._find_overview_comment(owner, repo, pr_number, headers)

        body = f"{summary}\n\n{COMMENT_MARKER}"

        try:
            if existing_comment:
                comment_id = existing_comment["id"]
                logger.info(f"Updating overview comment {comment_id}...")
                url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}"
                response = requests.patch(
                    url, headers=headers, json={"body": body}, timeout=30
                )
            else:
                logger.info("Creating new overview comment...")
                url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
                response = requests.post(
                    url, headers=headers, json={"body": body}, timeout=30
                )

            response.raise_for_status()
            logger.info("Successfully created/updated overview comment.")

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create or update overview comment: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")

    def _format_summary(self, summary: CodeReviewSummary) -> str:
        """Formats the structured summary into a markdown string."""
        parts = ["# Code Review Summary\n\n"]
        if summary.overview:
            parts.append(f"{summary.overview}\n\n")

        if summary.key_improvements:
            parts.append("### 🚀 Key Improvements\n")
            for item in summary.key_improvements:
                parts.append(f"- {item}\n")
            parts.append("\n")

        if summary.minor_suggestions:
            parts.append("### 💡 Minor Suggestions\n")
            for item in summary.minor_suggestions:
                parts.append(f"- {item}\n")
            parts.append("\n")

        if summary.critical_issues:
            parts.append("### 🚨 Critical Issues\n")
            for item in summary.critical_issues:
                parts.append(f"- {item}\n")
            parts.append("\n")

        return "".join(parts)

    def post_review(
        self, repository: Repository, pull_request: PullRequest, code_review: CodeReview
    ) -> Dict[str, Any]:
        """Orchestrates posting a complete code review to a GitHub pull request."""
        try:
            access_token = self.get_installation_access_token(
                repository.owner, repository.name
            )
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            if code_review.summary:
                formatted_summary = self._format_summary(code_review.summary)

                existing_comment = self._find_overview_comment(
                    repository.owner, repository.name, pull_request.number, headers
                )
                if existing_comment and not llm().is_summary_different(
                    summary_a=existing_comment["body"],
                    summary_b=formatted_summary,
                ):
                    logger.info(
                        f"PR #{pull_request.number} summary is semantically unchanged. Skipping update."
                    )
                else:
                    self._create_or_update_overview_comment(
                        repository.owner,
                        repository.name,
                        pull_request.number,
                        formatted_summary,
                        headers,
                    )

            comments = []
            if code_review.code_suggestions:
                for suggestion in code_review.code_suggestions:
                    if not suggestion or not suggestion.file_name:
                        continue

                    comment_body = suggestion.comment or ""
                    if suggestion.suggested_code:
                        comment_body += (
                            f"\n\n```suggestion\n{suggestion.suggested_code}\n```"
                        )

                    comment = {
                        "path": suggestion.file_name,
                        "body": comment_body,
                        "line": suggestion.line or 1,
                        "side": (
                            suggestion.side.value
                            if hasattr(suggestion, "side") and suggestion.side
                            else "RIGHT"
                        ),
                    }
                    if (
                        suggestion.start_line
                        and suggestion.line
                        and suggestion.start_line < suggestion.line
                    ):
                        comment["start_line"] = suggestion.start_line
                        comment["line"] = suggestion.line

                    comments.append(comment)

            review_body = "Review complete. See the overview comment for a summary."
            if not comments:
                logger.info("No valid code suggestions were generated to post.")
                review_body = "Review complete. No specific code suggestions were generated. See the overview comment for a summary."

            review_payload = {
                "body": review_body,
                "event": code_review.verdict.value,
                "comments": comments,
            }

            review_response_data = {}
            if comments or code_review.verdict != Verdict.COMMENT:
                response = requests.post(
                    f"https://api.github.com/repos/{repository.owner}/{repository.name}/pulls/{pull_request.number}/reviews",
                    headers=headers,
                    json=review_payload,
                    timeout=60,
                )
                response.raise_for_status()
                review_response_data = response.json()
            else:
                logger.info(
                    "No suggestions to post and verdict is COMMENT. Skipping formal review submission."
                )

            logger.info(f"Successfully posted review to PR #{pull_request.number}")
            return {
                "status": "success",
                "message": f"Review posted to GitHub for PR #{pull_request.number}",
                "review_data": review_response_data,
            }

        except (requests.exceptions.RequestException, ValueError) as e:
            error_msg = f"Error posting review to GitHub: {e}"
            if hasattr(e, "response") and e.response is not None:
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            return {
                "status": "error",
                "message": error_msg,
                "error_type": "github_api_error",
            }
        except Exception as e:
            error_msg = f"An unexpected error occurred: {e}"
            logger.exception(error_msg)
            return {
                "status": "error",
                "message": error_msg,
                "error_type": "unexpected_error",
            }

    def get_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Get the diff for a pull request by its number."""
        try:
            access_token = self.get_installation_access_token(owner, repo)
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3.diff",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
            logger.info(f"Requesting diff from API URL: {api_url}")
            response = requests.get(
                api_url,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            error_msg = (
                f"Failed to get diff for PR #{pr_number} from {owner}/{repo}: {e}"
            )
            if hasattr(e, "response") and e.response is not None:
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def get_diff_between_shas(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> str:
        """Get the diff between two SHAs by calling the compare API endpoint."""
        try:
            access_token = self.get_installation_access_token(owner, repo)
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3.diff",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            # Construct the correct API URL for comparing commits
            api_compare_url = f"https://api.github.com/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
            response = requests.get(api_compare_url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to get diff for {owner}/{repo} between {base_sha} and {head_sha}: {e}"
            if hasattr(e, "response") and e.response is not None:
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)
