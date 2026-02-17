"""
Repo Manager Plugin for SourceAnt.

Subscribes to PR and issue events to provide automated repository management:
- PR deduplication
- Issue deduplication
- Auto-labeling
"""

import json
import re
from typing import Dict, Any, Optional, List

from src.core.plugins import BasePlugin, PluginMetadata, PluginType
from src.core.plugins import event_hooks
from src.integrations.github.github import GitHub
from src.llms.llm_factory import llm
from src.models.config import Config
from src.config.settings import (
    REPO_MANAGER_ENABLED,
    REPO_MANAGER_PR_TRIAGE,
    REPO_MANAGER_ISSUE_TRIAGE,
    REPO_MANAGER_AUTO_LABEL,
)
from src.utils.logger import logger

from .prompts import RepoManagerPrompts

DEDUP_PR_MARKER = "<!-- SOURCEANT_DEDUP_CHECK -->"
DEDUP_ISSUE_MARKER = "<!-- SOURCEANT_ISSUE_DEDUP_CHECK -->"

MAX_DEDUP_CANDIDATES = 50
BODY_SNIPPET_LENGTH = 200
BODY_PROMPT_LENGTH = 1000
MAX_DIFF_LENGTH = 5000


class RepoManagerPlugin(BasePlugin):
    """
    Repo Manager Plugin that subscribes to PR and issue events.

    Provides automated deduplication detection and label suggestion.
    Only processes GitHub App events (not OAuth events).
    """

    _plugin_name = "repo_manager"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the Repo Manager plugin."""
        super().__init__(config)

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="repo_manager",
            version="1.0.0",
            description="Automated repository management: deduplication and auto-labeling",
            author="SourceAnt Team",
            plugin_type=PluginType.UTILITY,
            dependencies=[],
            config_schema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable/disable repo manager",
                        "default": False,
                    },
                    "pr_triage_enabled": {
                        "type": "boolean",
                        "description": "Enable PR triage (deduplication checks)",
                        "default": True,
                    },
                    "issue_triage_enabled": {
                        "type": "boolean",
                        "description": "Enable issue triage (deduplication checks)",
                        "default": True,
                    },
                    "auto_label_enabled": {
                        "type": "boolean",
                        "description": "Enable auto-labeling",
                        "default": True,
                    },
                },
            },
            enabled=False,
            priority=75,
        )

    async def _initialize(self) -> None:
        """Initialize the plugin."""
        logger.info("Initializing Repo Manager plugin")

        event_hooks.subscribe_to_events(
            plugin_name=self.metadata.name,
            callback=self._handle_event,
            event_types=[
                "pull_request.opened",
                "pull_request.reopened",
                "issues.opened",
                "issues.reopened",
            ],
        )

        logger.info("Repo Manager plugin initialized and subscribed to PR/issue events")

    async def _start(self) -> None:
        """Start the plugin."""
        logger.info("Starting Repo Manager plugin")

        try:
            llm_instance = llm()
            logger.info(f"Repo Manager using LLM: {llm_instance.__class__.__name__}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM for Repo Manager: {e}")
            raise

        logger.info("Repo Manager plugin started successfully")

    async def _stop(self) -> None:
        """Stop the plugin."""
        logger.info("Stopping Repo Manager plugin")
        logger.info("Repo Manager plugin stopped")

    async def _cleanup(self) -> None:
        """Cleanup plugin resources."""
        logger.info("Cleaning up Repo Manager plugin")
        logger.info("Repo Manager plugin cleanup completed")

    async def _handle_event(
        self, event_type: str, event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle PR and issue events."""
        try:
            auth_type = event_data.get("auth_type", "github_app")
            if auth_type != "github_app":
                logger.debug(
                    f"Skipping {event_type} from {auth_type} - "
                    "Repo Manager only processes GitHub App events"
                )
                return {
                    "processed": False,
                    "reason": "OAuth events not processed",
                }

            repository_event = event_data.get("repository_event")
            repository_context = event_data.get("repository_context")
            payload = event_data.get("payload", {})

            if not repository_event or not repository_context:
                return {"processed": False, "reason": "Missing required event data"}

            owner = repository_context.get("owner")
            repo = repository_context.get("name")
            full_name = repository_context.get("full_name", f"{owner}/{repo}")
            number = repository_event.get("number")
            title = repository_event.get("title", "")

            # Resolve config: entity config > env > plugin default
            repo_config = self._resolve_config(full_name)

            if not repo_config.get("enabled", False):
                return {
                    "processed": False,
                    "reason": "Repo Manager disabled for this repository",
                }

            # Determine item type and body
            is_pr = event_type.startswith("pull_request.")
            if is_pr:
                body = payload.get("pull_request", {}).get("body", "") or ""
            else:
                body = payload.get("issue", {}).get("body", "") or ""

            logger.info(f"Processing {event_type} for #{number} in {full_name}")

            github = GitHub()
            results = {}

            # Run dedup check
            if is_pr and repo_config.get("pr_triage_enabled", True):
                results["dedup"] = await self._check_pr_duplicates(
                    github, owner, repo, number, title, body
                )
            elif not is_pr and repo_config.get("issue_triage_enabled", True):
                results["dedup"] = await self._check_issue_duplicates(
                    github, owner, repo, number, title, body
                )

            # Run auto-label
            if repo_config.get("auto_label_enabled", True):
                diff_text = None
                if is_pr:
                    try:
                        diff_text = github.get_diff(
                            owner=owner, repo=repo, pr_number=number
                        )
                        if diff_text and len(diff_text) > MAX_DIFF_LENGTH:
                            diff_text = (
                                diff_text[:MAX_DIFF_LENGTH] + "\n... (truncated)"
                            )
                    except Exception as e:
                        logger.warning(f"Could not fetch diff for auto-label: {e}")

                results["auto_label"] = await self._auto_label(
                    github, owner, repo, number, title, body, diff_text
                )

            return {
                "processed": True,
                "number": number,
                "repository": full_name,
                "results": results,
            }

        except Exception as e:
            logger.error(f"Error processing {event_type} event: {e}", exc_info=True)
            return {"processed": False, "error": str(e)}

    def _resolve_config(self, full_name: str) -> Dict[str, Any]:
        """Resolve configuration: entity config > env var > plugin default."""
        defaults = {
            "enabled": REPO_MANAGER_ENABLED,
            "pr_triage_enabled": REPO_MANAGER_PR_TRIAGE,
            "issue_triage_enabled": REPO_MANAGER_ISSUE_TRIAGE,
            "auto_label_enabled": REPO_MANAGER_AUTO_LABEL,
        }

        config = dict(defaults)

        # Override with entity-level config
        for key in defaults:
            db_key = f"repo_manager.{key}"
            value = Config.get_value("repository", full_name, db_key)
            if value is not None:
                config[key] = value

        return config

    async def _check_pr_duplicates(
        self,
        github: GitHub,
        owner: str,
        repo: str,
        pr_number: int,
        title: str,
        body: str,
    ) -> Dict[str, Any]:
        """Check for duplicate PRs and post a comment if found."""
        try:
            open_prs = github.list_open_pull_requests(owner, repo)
            other_prs = [pr for pr in open_prs if pr.get("number") != pr_number]
            other_prs = other_prs[:MAX_DEDUP_CANDIDATES]

            if not other_prs:
                return {"duplicates": [], "status": "no_other_prs"}

            existing_text = "\n".join(
                f"- PR #{pr['number']}: {pr.get('title', 'No title')} "
                f"(Body: {(pr.get('body') or '')[:BODY_SNIPPET_LENGTH]})"
                for pr in other_prs
            )

            prompt = RepoManagerPrompts.PR_DEDUP_PROMPT.format(
                new_title=title,
                new_body=body[:BODY_PROMPT_LENGTH] if body else "(no description)",
                existing_prs=existing_text,
            )

            response = llm().generate_text(prompt)
            duplicates = self._parse_dedup_response(response)

            if duplicates:
                comment_body = self._format_dedup_comment(duplicates, "PR")
                await self._post_or_update_comment(
                    github, owner, repo, pr_number, comment_body, DEDUP_PR_MARKER
                )

            return {"duplicates": duplicates, "status": "checked"}

        except Exception as e:
            logger.error(f"PR dedup check failed: {e}", exc_info=True)
            return {"duplicates": [], "status": "error", "error": str(e)}

    async def _check_issue_duplicates(
        self,
        github: GitHub,
        owner: str,
        repo: str,
        issue_number: int,
        title: str,
        body: str,
    ) -> Dict[str, Any]:
        """Check for duplicate issues and post a comment if found."""
        try:
            open_issues = github.list_open_issues(owner, repo)
            other_issues = [
                issue for issue in open_issues if issue.get("number") != issue_number
            ]
            other_issues = other_issues[:MAX_DEDUP_CANDIDATES]

            if not other_issues:
                return {"duplicates": [], "status": "no_other_issues"}

            existing_text = "\n".join(
                f"- Issue #{issue['number']}: {issue.get('title', 'No title')} "
                f"(Body: {(issue.get('body') or '')[:BODY_SNIPPET_LENGTH]})"
                for issue in other_issues
            )

            prompt = RepoManagerPrompts.ISSUE_DEDUP_PROMPT.format(
                new_title=title,
                new_body=body[:BODY_PROMPT_LENGTH] if body else "(no description)",
                existing_issues=existing_text,
            )

            response = llm().generate_text(prompt)
            duplicates = self._parse_dedup_response(response)

            if duplicates:
                comment_body = self._format_dedup_comment(duplicates, "issue")
                await self._post_or_update_comment(
                    github,
                    owner,
                    repo,
                    issue_number,
                    comment_body,
                    DEDUP_ISSUE_MARKER,
                )

            return {"duplicates": duplicates, "status": "checked"}

        except Exception as e:
            logger.error(f"Issue dedup check failed: {e}", exc_info=True)
            return {"duplicates": [], "status": "error", "error": str(e)}

    async def _auto_label(
        self,
        github: GitHub,
        owner: str,
        repo: str,
        number: int,
        title: str,
        body: str,
        diff_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Auto-label an issue or PR based on content."""
        try:
            repo_labels = github.list_labels(owner, repo)

            if not repo_labels:
                return {"labels": [], "status": "no_labels_in_repo"}

            label_names = [label["name"] for label in repo_labels]
            available_labels = ", ".join(label_names)

            diff_section = ""
            if diff_text:
                diff_section = f"\n**Diff summary:**\n```\n{diff_text}\n```"

            prompt = RepoManagerPrompts.AUTO_LABEL_PROMPT.format(
                title=title,
                body=body[:BODY_PROMPT_LENGTH] if body else "(no description)",
                diff_section=diff_section,
                available_labels=available_labels,
            )

            response = llm().generate_text(prompt)
            suggested_labels = self._parse_label_response(response, label_names)

            if suggested_labels:
                github.add_labels(owner, repo, number, suggested_labels)

            return {"labels": suggested_labels, "status": "labeled"}

        except Exception as e:
            logger.error(f"Auto-label failed: {e}", exc_info=True)
            return {"labels": [], "status": "error", "error": str(e)}

    def _parse_dedup_response(self, response: str) -> List[int]:
        """Parse LLM response for duplicate numbers. Returns list of ints."""
        try:
            parsed = json.loads(response.strip())
            if isinstance(parsed, list):
                return [int(n) for n in parsed if isinstance(n, (int, float))]
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: extract numbers using regex
        matches = re.findall(r"#?(\d+)", response)
        return [int(m) for m in matches] if matches else []

    def _parse_label_response(self, response: str, repo_labels: List[str]) -> List[str]:
        """Parse LLM response for labels and validate against repo labels."""
        try:
            parsed = json.loads(response.strip())
            if isinstance(parsed, list):
                suggested = [str(label) for label in parsed]
            else:
                suggested = []
        except (json.JSONDecodeError, ValueError):
            suggested = []

        # Case-insensitive validation
        label_map = {label.lower(): label for label in repo_labels}
        valid_labels = []
        for label in suggested:
            matched = label_map.get(label.lower())
            if matched:
                valid_labels.append(matched)

        return valid_labels

    def _format_dedup_comment(self, duplicates: List[int], item_type: str) -> str:
        """Format a markdown comment listing potential duplicates."""
        lines = [
            f"**Potential Duplicate {item_type.upper()}s Detected**\n",
            f"This {item_type} may be related to the following open {item_type}(s):\n",
        ]
        for number in duplicates:
            lines.append(f"- #{number}")

        lines.append(f"\nPlease review and close if this is a duplicate.")
        return "\n".join(lines)

    async def _post_or_update_comment(
        self,
        github: GitHub,
        owner: str,
        repo: str,
        number: int,
        body: str,
        marker: str,
    ) -> None:
        """Post or update an idempotent comment using a marker."""
        full_body = f"{body}\n\n{marker}"

        existing = github.find_comment_with_marker(owner, repo, number, marker)
        if existing:
            github.update_comment(owner, repo, existing["id"], full_body)
        else:
            github.post_issue_comment(owner, repo, number, full_body)
