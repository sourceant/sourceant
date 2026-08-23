"""
GitHub webhook payload parser.

Extracts user, repository, and activity data from GitHub webhook payloads.
"""

from typing import Dict, Any, Optional
from src.utils.logger import logger


class GitHubWebhookParser:
    """
    Parses GitHub webhook payloads to extract structured data.

    Used by the event dispatcher to extract user, repository, and activity
    information from both GitHub App and OAuth webhook payloads.
    """

    def get_user_info_from_webhook(
        self, webhook_payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Extract user information from webhook payload.

        Args:
            webhook_payload: GitHub webhook payload

        Returns:
            User information dictionary
        """
        try:
            user_info = None

            # Try different sources for user info
            if "sender" in webhook_payload:
                user_info = webhook_payload["sender"]
            elif "pusher" in webhook_payload:
                user_info = webhook_payload["pusher"]
            elif (
                "pull_request" in webhook_payload
                and "user" in webhook_payload["pull_request"]
            ):
                user_info = webhook_payload["pull_request"]["user"]
            elif "issue" in webhook_payload and "user" in webhook_payload["issue"]:
                user_info = webhook_payload["issue"]["user"]

            if user_info:
                return {
                    "github_id": user_info.get("id"),
                    "username": user_info.get("login") or user_info.get("name"),
                    "avatar_url": user_info.get("avatar_url"),
                    "url": user_info.get("url"),
                    "type": user_info.get("type"),
                    "email": user_info.get("email"),  # Available for some event types
                }

            return None

        except Exception as e:
            logger.error(f"Error extracting user info from webhook: {e}")
            return None

    def get_repository_info_from_webhook(
        self, webhook_payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Extract repository information from webhook payload.

        Args:
            webhook_payload: GitHub webhook payload

        Returns:
            Repository information dictionary
        """
        try:
            repo_info = webhook_payload.get("repository")
            if not repo_info:
                return None

            return {
                "github_repo_id": repo_info.get("id"),
                "full_name": repo_info.get("full_name"),
                "name": repo_info.get("name"),
                "owner": repo_info.get("owner", {}).get("login"),
                "private": repo_info.get("private", False),
                "clone_url": repo_info.get("clone_url"),
                "ssh_url": repo_info.get("ssh_url"),
                "default_branch": repo_info.get("default_branch"),
                "description": repo_info.get("description"),
                "language": repo_info.get("language"),
                "stargazers_count": repo_info.get("stargazers_count"),
                "forks_count": repo_info.get("forks_count"),
            }

        except Exception as e:
            logger.error(f"Error extracting repository info from webhook: {e}")
            return None

    def extract_activity_data(
        self, webhook_payload: Dict[str, Any], event_type: str
    ) -> Dict[str, Any]:
        """
        Extract activity data from webhook for tracking purposes.

        Args:
            webhook_payload: GitHub webhook payload
            event_type: GitHub event type

        Returns:
            Activity data dictionary
        """
        try:
            activity_data = {
                "event_type": event_type,
                "action": webhook_payload.get("action"),
                "created_at": webhook_payload.get("created_at"),
                "user": self.get_user_info_from_webhook(webhook_payload),
                "repository": self.get_repository_info_from_webhook(webhook_payload),
            }

            # Add event-specific data
            if event_type == "push":
                activity_data.update(
                    {
                        "ref": webhook_payload.get("ref"),
                        "before": webhook_payload.get("before"),
                        "after": webhook_payload.get("after"),
                        "commits_count": len(webhook_payload.get("commits", [])),
                        "forced": webhook_payload.get("forced", False),
                    }
                )

            elif event_type == "pull_request":
                pr = webhook_payload.get("pull_request", {})
                activity_data.update(
                    {
                        "pull_request": {
                            "number": pr.get("number"),
                            "title": pr.get("title"),
                            "state": pr.get("state"),
                            "draft": pr.get("draft"),
                            "merged": pr.get("merged"),
                            "base_ref": pr.get("base", {}).get("ref"),
                            "head_ref": pr.get("head", {}).get("ref"),
                        }
                    }
                )

            elif event_type == "issues":
                issue = webhook_payload.get("issue", {})
                activity_data.update(
                    {
                        "issue": {
                            "number": issue.get("number"),
                            "title": issue.get("title"),
                            "state": issue.get("state"),
                            "labels": [
                                label.get("name") for label in issue.get("labels", [])
                            ],
                        }
                    }
                )

            elif event_type == "release":
                release = webhook_payload.get("release", {})
                activity_data.update(
                    {
                        "release": {
                            "tag_name": release.get("tag_name"),
                            "name": release.get("name"),
                            "draft": release.get("draft"),
                            "prerelease": release.get("prerelease"),
                            "published_at": release.get("published_at"),
                        }
                    }
                )

            elif event_type == "star":
                activity_data.update({"starred_at": webhook_payload.get("starred_at")})

            elif event_type == "fork":
                forkee = webhook_payload.get("forkee", {})
                activity_data.update(
                    {
                        "forkee": {
                            "full_name": forkee.get("full_name"),
                            "clone_url": forkee.get("clone_url"),
                        }
                    }
                )

            elif event_type == "watch":
                activity_data.update({"started_at": webhook_payload.get("created_at")})

            # Add any additional metadata
            activity_data["metadata"] = {
                "delivery_id": None,  # Will be set by webhook handler
                "webhook_timestamp": None,  # Will be set by webhook handler
                "source": "github_oauth",
            }

            return activity_data

        except Exception as e:
            logger.error(
                f"Error extracting activity data from {event_type} webhook: {e}"
            )
            return {
                "event_type": event_type,
                "error": str(e),
                "user": self.get_user_info_from_webhook(webhook_payload),
                "repository": self.get_repository_info_from_webhook(webhook_payload),
                "metadata": {"source": "github_oauth", "extraction_failed": True},
            }
