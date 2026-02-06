import jwt
import time
import requests
import os
import base64
from typing import Dict, Any, Optional
from dateutil.parser import isoparse
from ..provider_adapter import ProviderAdapter
from src.models.code_review import CodeReview, CodeReviewSummary, Verdict
from src.models.repository import Repository

from src.utils.line_mapper import LineMapper
from src.models.pull_request import PullRequest
from src.utils.logger import logger
from src.llms.llm_factory import llm


COMMENT_MARKER = "<!-- SOURCEANT_REVIEW_SUMMARY -->"
FALLBACK_COMMENT_MARKER = "<!-- SOURCEANT_FALLBACK_REVIEW -->"


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

    def has_existing_bot_approval(self, owner: str, repo: str, pr_number: int) -> bool:
        try:
            access_token = self.get_installation_access_token(owner, repo)
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            app_slug = self.get_app_slug()
            bot_login = f"{app_slug}[bot]"
            response = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            for review in response.json():
                user = review.get("user", {})
                if user.get("login") == bot_login and review.get("state") == "APPROVED":
                    return True
            return False
        except Exception as e:
            logger.warning(f"Could not check for existing approvals: {e}")
            return False

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

    def _find_fallback_comment(
        self, owner: str, repo: str, pr_number: int, headers: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Find the bot's fallback review comment on a PR."""
        try:
            response = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            comments = response.json()

            for comment in comments:
                if FALLBACK_COMMENT_MARKER in comment.get("body", ""):
                    logger.info(
                        f"Found previous fallback comment with ID: {comment['id']}"
                    )
                    return comment

            logger.info("No previous fallback comment found.")
            return None

        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"Could not search for previous fallback comment: {e}")
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

    def _post_review_as_fallback_comment(
        self,
        repository: Repository,
        pull_request: PullRequest,
        code_review: CodeReview,
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        """Posts or updates actionable code review as a regular PR comment."""
        try:
            comment_body = "## 🔍 Code Review\n\n"

            # Add code suggestions in expandable, collapsed sections
            if code_review.code_suggestions:
                for i, suggestion in enumerate(code_review.code_suggestions, 1):
                    if not suggestion.file_name:
                        continue

                    # Create expandable section for each suggestion
                    file_location = f"**{suggestion.file_name}**"
                    if suggestion.start_line == suggestion.end_line:
                        file_location += f" (Line {suggestion.start_line})"
                    else:
                        file_location += (
                            f" (Lines {suggestion.start_line}-{suggestion.end_line})"
                        )

                    # Collapsed by default with clear title
                    comment_body += f"<details>\n<summary>💡 {i}. {file_location}"
                    if hasattr(suggestion, "category") and suggestion.category:
                        comment_body += f" - {suggestion.category.value}"
                    comment_body += "</summary>\n\n"

                    if suggestion.comment:
                        comment_body += f"{suggestion.comment}\n\n"

                    if suggestion.suggested_code:
                        comment_body += f"**Suggested Code:**\n```suggestion\n{suggestion.suggested_code}\n```\n\n"

                    if suggestion.existing_code:
                        comment_body += f"**Current Code:**\n```python\n{suggestion.existing_code}\n```\n\n"

                    comment_body += "</details>\n\n"

            # Add other review sections as collapsed expandable sections if present
            sections = [
                ("🐛 Potential Bugs", code_review.potential_bugs),
                ("⚡ Performance", code_review.performance),
                ("🛡️ Security", code_review.security),
                ("📖 Readability", code_review.readability),
                ("🔧 Refactoring", code_review.refactoring_suggestions),
                ("📚 Documentation", code_review.documentation_suggestions),
            ]

            for title, content in sections:
                if content and content.strip():
                    comment_body += f"<details>\n<summary>{title}</summary>\n\n{content}\n\n</details>\n\n"

            comment_body += f"**Verdict:** {code_review.verdict.value}\n\n"
            comment_body += f"*Posted as a comment because posting a review failed.*\n\n{FALLBACK_COMMENT_MARKER}"

            # Check for existing fallback comment to update
            existing_comment = self._find_fallback_comment(
                repository.owner, repository.name, pull_request.number, headers
            )

            if existing_comment:
                comment_id = existing_comment["id"]
                logger.info(f"Updating existing fallback comment {comment_id}...")
                url = f"https://api.github.com/repos/{repository.owner}/{repository.name}/issues/comments/{comment_id}"
                response = requests.patch(
                    url, headers=headers, json={"body": comment_body}, timeout=30
                )
            else:
                logger.info("Creating new fallback comment...")
                url = f"https://api.github.com/repos/{repository.owner}/{repository.name}/issues/{pull_request.number}/comments"
                response = requests.post(
                    url, headers=headers, json={"body": comment_body}, timeout=30
                )

            response.raise_for_status()
            comment_data = response.json()
            logger.info(
                f"Successfully posted/updated fallback comment with ID: {comment_data.get('id')}"
            )

            return {
                "status": "success",
                "comment_id": comment_data.get("id"),
                "message": "Review posted as fallback comment",
            }

        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to post fallback comment: {e}"
            if hasattr(e, "response") and e.response is not None:
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}
        except Exception as e:
            error_msg = f"Unexpected error in fallback comment: {e}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}

    def post_review(
        self,
        repository: Repository,
        pull_request: PullRequest,
        code_review: CodeReview,
        line_mapper: LineMapper,
    ) -> Dict[str, Any]:
        """Orchestrates posting a complete code review to a GitHub pull request."""
        if pull_request.number is None:
            error_msg = "Cannot post review without a valid pull request number."
            logger.error(error_msg)
            return {
                "status": "error",
                "message": error_msg,
                "error_type": "missing_pr_number",
            }

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

                    # Use the line_mapper to get a valid position
                    mapped_result = line_mapper.validate_and_map_suggestion(suggestion)
                    if not mapped_result:
                        logger.warning(
                            f"Could not map suggestion to a valid line: {suggestion}"
                        )
                        continue

                    position, _ = mapped_result

                    comment_body = suggestion.comment or ""
                    if suggestion.suggested_code:
                        comment_body += (
                            f"\n\n```suggestion\n{suggestion.suggested_code}\n```"
                        )

                    comment = {
                        "path": suggestion.file_name,
                        "body": comment_body,
                    }

                    # For multi-line suggestions, use start_line and line.
                    # Otherwise, use the diff-based position.
                    if (
                        suggestion.start_line
                        and suggestion.end_line
                        and suggestion.start_line < suggestion.end_line
                    ):
                        comment["start_line"] = suggestion.start_line
                        comment["line"] = suggestion.end_line
                        comment["side"] = (
                            suggestion.side.value if suggestion.side else "RIGHT"
                        )
                        comment["start_side"] = (
                            suggestion.side.value if suggestion.side else "RIGHT"
                        )
                    else:
                        comment["position"] = position

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

            # Fallback: Post review content as a comment when API review posting fails
            logger.info(
                "Attempting fallback: posting review as comment instead of formal review"
            )

            # Always update the overview comment even when fallback is used
            if code_review.summary:
                formatted_summary = self._format_summary(code_review.summary)
                existing_comment = self._find_overview_comment(
                    repository.owner, repository.name, pull_request.number, headers
                )
                if not existing_comment or llm().is_summary_different(
                    summary_a=existing_comment["body"],
                    summary_b=formatted_summary,
                ):
                    self._create_or_update_overview_comment(
                        repository.owner,
                        repository.name,
                        pull_request.number,
                        formatted_summary,
                        headers,
                    )

            fallback_result = self._post_review_as_fallback_comment(
                repository, pull_request, code_review, headers
            )
            if fallback_result["status"] == "success":
                return {
                    "status": "partial_success",
                    "message": f"Review API failed, but posted as comment: {error_msg}",
                    "error_type": "github_api_error_with_fallback",
                    "fallback_comment_id": fallback_result.get("comment_id"),
                }
            else:
                return {
                    "status": "error",
                    "message": f"{error_msg}. Fallback comment also failed: {fallback_result['message']}",
                    "error_type": "github_api_error",
                }
        except Exception as e:
            error_msg = f"An unexpected error occurred: {e}"
            logger.exception(error_msg)

            # Fallback for unexpected errors too
            try:
                logger.info(
                    "Attempting fallback after unexpected error: posting review as comment"
                )
                fallback_result = self._post_review_as_fallback_comment(
                    repository, pull_request, code_review, headers
                )
                if fallback_result["status"] == "success":
                    return {
                        "status": "partial_success",
                        "message": f"Unexpected error occurred, but posted as comment: {error_msg}",
                        "error_type": "unexpected_error_with_fallback",
                        "fallback_comment_id": fallback_result.get("comment_id"),
                    }
            except Exception as fallback_e:
                logger.error(f"Fallback comment posting also failed: {fallback_e}")

            return {
                "status": "error",
                "message": error_msg,
                "error_type": "unexpected_error",
            }

    def get_diff(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        base_sha: Optional[str] = None,
        head_sha: Optional[str] = None,
    ) -> str:
        """Get the diff for a pull request.

        If base_sha and head_sha are provided, it fetches the diff by comparing them.
        Otherwise, it falls back to fetching the diff by the pull request number.
        """
        if base_sha and head_sha:
            logger.info(
                f"Fetching diff for {owner}/{repo} between {base_sha} and {head_sha}"
            )
            return self.get_diff_between_shas(owner, repo, base_sha, head_sha)

        logger.info(f"Fetching diff for PR #{pr_number} from {owner}/{repo}")
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

    def get_file_content(
        self, owner: str, repo: str, file_path: str, sha: str
    ) -> Optional[str]:
        """Get the raw content of a file from a repository at a specific commit SHA."""
        try:
            access_token = self.get_installation_access_token(owner, repo)
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            api_url = (
                f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
            )
            params = {"ref": sha}

            logger.info(f"Requesting file content from {api_url} at ref {sha}")

            response = requests.get(
                api_url,
                headers=headers,
                params=params,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            if "content" not in data:
                logger.error(f"No 'content' field in response for {file_path}")
                return None

            # Content is Base64 encoded
            encoded_content = data["content"]
            decoded_content = base64.b64decode(encoded_content).decode("utf-8")

            return decoded_content
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"File not found: {file_path} at SHA {sha}.")
                return None
            error_msg = (
                f"Failed to get file content for {file_path}: {e} - {e.response.text}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        except (base64.B64DecodeError, UnicodeDecodeError) as e:
            error_msg = f"Failed to decode file content for {file_path}: {e}"
            logger.error(error_msg)
            raise ValueError(error_msg)
