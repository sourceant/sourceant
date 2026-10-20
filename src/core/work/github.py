"""Work kept in GitHub Issues.

Status and priority are labels under a prefix, the convention many teams
already use (`status: in progress`, `priority: high`). Done is a closed issue,
so nothing outside SourceAnt has to learn a new label to see that work ended.
"""

from __future__ import annotations

from typing import Callable

import httpx

from src.utils.concurrency import gather_bounded
from src.utils.provider_pages import fetch_all

from . import (
    PRIORITIES,
    Person,
    Priority,
    Status,
    TrackerError,
    WorkChange,
    WorkDraft,
    WorkItem,
    WorkQuery,
)

API = "https://api.github.com"
STATUS_PREFIX = "status: "
PRIORITY_PREFIX = "priority: "
STATUS_LABEL: dict[str, str] = {
    "todo": "todo",
    "in_progress": "in progress",
    "in_review": "in review",
}
LABEL_COLOUR = {
    "todo": "d4d9cf",
    "in progress": "2f74c0",
    "in review": "8a6bd0",
    "urgent": "b60205",
    "high": "d93f0b",
    "normal": "fbca04",
    "low": "c5def5",
}
FINDING_LABEL = "sourceant: finding"


def status_of(issue: dict, labels: list[str]) -> Status:
    if issue.get("state") == "closed":
        return "done"
    for label in labels:
        if label.lower().startswith(STATUS_PREFIX):
            value = label[len(STATUS_PREFIX) :].strip().lower()
            for status, text in STATUS_LABEL.items():
                if value == text:
                    return status  # type: ignore[return-value]
    if "pull_request" in issue:
        return "in_review"
    return "todo"


def priority_of(labels: list[str]) -> Priority | None:
    for label in labels:
        if label.lower().startswith(PRIORITY_PREFIX):
            value = label[len(PRIORITY_PREFIX) :].strip().lower()
            if value in PRIORITIES:
                return value  # type: ignore[return-value]
    return None


def item_of(repo: str, issue: dict) -> WorkItem:
    labels = [label["name"] for label in issue.get("labels", [])]
    kind = (
        "pull"
        if "pull_request" in issue
        else "finding" if FINDING_LABEL in labels else "issue"
    )
    return WorkItem(
        tracker="github",
        repo=repo,
        number=issue["number"],
        kind=kind,
        title=issue.get("title") or "",
        url=issue.get("html_url"),
        status=status_of(issue, labels),
        priority=priority_of(labels),
        assignees=[a["login"] for a in issue.get("assignees") or []],
        labels=labels,
        author=(issue.get("user") or {}).get("login"),
        comments=issue.get("comments", 0),
        updated_at=issue.get("updated_at"),
    )


def matches(item: WorkItem, query: WorkQuery) -> bool:
    if query.status and item.status not in query.status:
        return False
    if not query.status and not query.include_done and item.status == "done":
        return False
    if query.priority and item.priority not in query.priority:
        return False
    if query.assignee and query.assignee not in item.assignees:
        return False
    if query.kind and item.kind not in query.kind:
        return False
    if query.text and query.text.lower() not in item.title.lower():
        return False
    return True


class GitHubTracker:
    name = "github"

    def __init__(
        self,
        token: str,
        client: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=20),
    ):
        self._headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }
        self._client = client

    async def items(self, repos: list[str], query: WorkQuery) -> list[WorkItem]:
        state = "all" if query.include_done or "done" in query.status else "open"

        async def of(client: httpx.AsyncClient, repo: str):
            issues, truncated = await fetch_all(
                client,
                f"{API}/repos/{repo}/issues",
                self._headers,
                params={"state": state, "sort": "updated"},
            )
            return repo, issues, truncated

        async with self._client() as client:
            gathered = await gather_bounded(
                [lambda c=client, r=r: of(c, r) for r in dict.fromkeys(repos)]
            )
        if gathered and all(
            truncated and not found for _, found, truncated in gathered
        ):
            raise TrackerError("GitHub did not answer")
        found = [
            item_of(repo, issue) for repo, issues, _ in gathered for issue in issues
        ]
        found = [item for item in found if matches(item, query)]
        found.sort(key=lambda item: item.updated_at or "", reverse=True)
        return found

    async def _ensure_label(self, client: httpx.AsyncClient, repo: str, name: str):
        existing = await client.get(
            f"{API}/repos/{repo}/labels/{name}", headers=self._headers
        )
        if existing.status_code == 200:
            return
        value = name.split(": ", 1)[-1]
        created = await client.post(
            f"{API}/repos/{repo}/labels",
            headers=self._headers,
            json={"name": name, "color": LABEL_COLOUR.get(value, "ededed")},
        )
        # Someone else may have made it between the two calls; that is fine.
        if created.status_code not in (201, 422):
            raise TrackerError(f"Could not create the label {name}")

    async def update(self, repo: str, number: int, change: WorkChange) -> WorkItem:
        base = f"{API}/repos/{repo}/issues/{number}"
        async with self._client() as client:
            current = await client.get(base, headers=self._headers)
            if current.status_code != 200:
                raise TrackerError("The item could not be read")
            issue = current.json()
            labels = [label["name"] for label in issue.get("labels", [])]
            patch: dict = {}
            wanted = list(labels)
            if change.status is not None:
                wanted = [l for l in wanted if not l.lower().startswith(STATUS_PREFIX)]
                if change.status == "done":
                    patch["state"] = "closed"
                else:
                    patch["state"] = "open"
                    wanted.append(STATUS_PREFIX + STATUS_LABEL[change.status])
            if change.priority is not None or change.clear_priority:
                wanted = [
                    l for l in wanted if not l.lower().startswith(PRIORITY_PREFIX)
                ]
                if change.priority is not None:
                    wanted.append(PRIORITY_PREFIX + change.priority)
            wanted = [l for l in wanted if l not in change.remove_labels]
            wanted += [l for l in change.add_labels if l not in wanted]
            if wanted != labels:
                for name in set(wanted) - set(labels):
                    if name.startswith((STATUS_PREFIX, PRIORITY_PREFIX)):
                        await self._ensure_label(client, repo, name)
                patch["labels"] = wanted
            if change.assignees is not None:
                patch["assignees"] = change.assignees
            if not patch:
                return item_of(repo, issue)
            saved = await client.patch(base, headers=self._headers, json=patch)
            if saved.status_code >= 300:
                raise TrackerError("GitHub refused the change")
            return item_of(repo, saved.json())

    async def comment(self, repo: str, number: int, body: str) -> None:
        async with self._client() as client:
            sent = await client.post(
                f"{API}/repos/{repo}/issues/{number}/comments",
                headers=self._headers,
                json={"body": body},
            )
        if sent.status_code >= 300:
            raise TrackerError("GitHub refused the comment")

    async def create(self, repo: str, draft: WorkDraft) -> WorkItem:
        labels = list(dict.fromkeys(draft.labels))
        if draft.priority:
            labels.append(PRIORITY_PREFIX + draft.priority)
        async with self._client() as client:
            for name in labels:
                if name.startswith(PRIORITY_PREFIX) or name == FINDING_LABEL:
                    await self._ensure_label(client, repo, name)
            made = await client.post(
                f"{API}/repos/{repo}/issues",
                headers=self._headers,
                json={
                    "title": draft.title,
                    "body": draft.body,
                    "labels": labels,
                    "assignees": draft.assignees,
                },
            )
        if made.status_code >= 300:
            raise TrackerError("GitHub refused the new issue")
        return item_of(repo, made.json())

    async def people(self, repo: str) -> list[Person]:
        async with self._client() as client:
            found, _ = await fetch_all(
                client, f"{API}/repos/{repo}/assignees", self._headers
            )
        return [Person(login=p["login"], avatar_url=p.get("avatar_url")) for p in found]
