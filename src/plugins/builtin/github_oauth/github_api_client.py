"""
GitHub OAuth API client for making authenticated API calls.
"""

import requests
from typing import Dict, Any, Optional
from src.utils.logger import logger


class GitHubOAuthApiClient:
    """
    GitHub OAuth API client.

    Makes API calls using OAuth app credentials on behalf of users.
    For webhook payload parsing, use GitHubWebhookParser instead.
    """

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.github_api_base = "https://api.github.com"

    async def get_pull_request_diff(
        self, owner: str, repo: str, pr_number: int
    ) -> Optional[str]:
        """
        Get pull request diff using OAuth app credentials.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number

        Returns:
            Diff content as string
        """
        try:
            url = f"{self.github_api_base}/repos/{owner}/{repo}/pulls/{pr_number}"

            headers = {
                "Accept": "application/vnd.github.v3.diff",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "SourceAnt-OAuth-App",
            }

            # Use basic auth with OAuth app credentials
            auth = (self.client_id, self.client_secret)

            response = requests.get(url, headers=headers, auth=auth, timeout=30)
            response.raise_for_status()

            return response.text

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get PR diff for {owner}/{repo}#{pr_number}: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting PR diff: {e}")
            return None

    async def get_file_content(
        self, owner: str, repo: str, file_path: str, ref: str
    ) -> Optional[str]:
        """
        Get file content from repository.

        Args:
            owner: Repository owner
            repo: Repository name
            file_path: Path to file
            ref: Git reference (commit SHA, branch, tag)

        Returns:
            File content as string
        """
        try:
            url = f"{self.github_api_base}/repos/{owner}/{repo}/contents/{file_path}"

            headers = {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "SourceAnt-OAuth-App",
            }

            params = {"ref": ref}
            auth = (self.client_id, self.client_secret)

            response = requests.get(
                url, headers=headers, auth=auth, params=params, timeout=30
            )
            response.raise_for_status()

            data = response.json()

            # Decode base64 content
            import base64

            if "content" in data:
                content = base64.b64decode(data["content"]).decode("utf-8")
                return content

            return None

        except requests.exceptions.RequestException as e:
            logger.error(
                f"Failed to get file content for {owner}/{repo}/{file_path}@{ref}: {e}"
            )
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting file content: {e}")
            return None

    async def post_review_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        commit_sha: str,
        path: str,
        position: Optional[int] = None,
        line: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Post a review comment on a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            body: Comment body
            commit_sha: SHA of the commit
            path: File path
            position: Position in diff (for diff-based comments)
            line: Line number (for line-based comments)

        Returns:
            Comment response or None if failed
        """
        try:
            url = f"{self.github_api_base}/repos/{owner}/{repo}/pulls/{pr_number}/comments"

            headers = {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "SourceAnt-OAuth-App",
                "Content-Type": "application/json",
            }

            comment_data = {"body": body, "commit_id": commit_sha, "path": path}

            if position is not None:
                comment_data["position"] = position
            elif line is not None:
                comment_data["line"] = line
                comment_data["side"] = "RIGHT"

            auth = (self.client_id, self.client_secret)

            response = requests.post(
                url, headers=headers, auth=auth, json=comment_data, timeout=30
            )
            response.raise_for_status()

            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to post review comment: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error posting review comment: {e}")
            return None

    async def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        event: str = "COMMENT",
        comments: Optional[list] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a pull request review.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            body: Review body
            event: Review event (COMMENT, APPROVE, REQUEST_CHANGES)
            comments: List of review comments

        Returns:
            Review response or None if failed
        """
        try:
            url = (
                f"{self.github_api_base}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
            )

            headers = {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "SourceAnt-OAuth-App",
                "Content-Type": "application/json",
            }

            review_data = {"body": body, "event": event}

            if comments:
                review_data["comments"] = comments

            auth = (self.client_id, self.client_secret)

            response = requests.post(
                url, headers=headers, auth=auth, json=review_data, timeout=30
            )
            response.raise_for_status()

            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create PR review: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error creating PR review: {e}")
            return None
