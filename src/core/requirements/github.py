from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.core.scope import Scope

from .models import Requirement

DEFAULT_LABELS = ("requirement", "acceptance-criteria")
OPEN = "open"
MET = "met"


class GitHubIssueRequirements:
    """Requirements a team already writes as GitHub issues.

    Reads issues carrying one of the configured labels. Nothing is written back
    to GitHub, so an issue stays the place the team edits it.
    """

    def __init__(
        self,
        issues,
        *,
        labels: Iterable[str] = DEFAULT_LABELS,
        repository_key: str = "repository",
    ) -> None:
        self._issues = issues
        self._labels = frozenset(label for label in labels if label)
        self._repository_key = repository_key

    def sync(self, scope: Scope) -> tuple[Requirement, ...]:
        repository = scope.get(self._repository_key)
        if not repository:
            raise ValueError(f"scope must carry a {self._repository_key}")
        found = []
        for issue in self._issues(repository, sorted(self._labels)) or ():
            requirement = _requirement_from_issue(issue)
            if requirement is not None:
                found.append(requirement)
        return tuple(found)


def _requirement_from_issue(issue: Mapping[str, Any]) -> Requirement | None:
    if not isinstance(issue, Mapping):
        return None
    number = issue.get("number")
    title = issue.get("title")
    if number is None or not isinstance(title, str) or not title:
        return None
    labels = tuple(
        label.get("name", "") if isinstance(label, Mapping) else str(label)
        for label in issue.get("labels") or ()
    )
    return Requirement(
        id=f"issue-{number}",
        kind="requirement",
        status=MET if issue.get("state") == "closed" else OPEN,
        summary=title,
        external_ref=str(issue.get("html_url") or number),
        properties={
            "labels": [label for label in labels if label],
            "issue_number": number,
        },
    )
