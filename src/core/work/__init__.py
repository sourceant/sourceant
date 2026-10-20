"""Work: the issues, pull requests and tracked findings a team acts on.

Pages read and change work through one interface, so where it is kept (GitHub
Issues today; SourceAnt itself, Linear or Jira later) is a choice of tracker,
not a change to the pages.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

Status = Literal["todo", "in_progress", "in_review", "done"]
Priority = Literal["urgent", "high", "normal", "low"]
Kind = Literal["issue", "pull", "finding"]
# Where the item stands on GitHub, which is what its colour shows: an open or
# draft pull, a merged or closed one, an issue done or closed as not planned.
State = Literal["open", "draft", "merged", "closed", "completed", "not_planned"]

STATUSES: tuple[Status, ...] = ("todo", "in_progress", "in_review", "done")
PRIORITIES: tuple[Priority, ...] = ("urgent", "high", "normal", "low")


class Person(BaseModel):
    login: str
    avatar_url: str | None = None


class WorkItem(BaseModel):
    tracker: str
    repo: str
    number: int
    kind: Kind
    title: str
    url: str | None = None
    status: Status
    state: State = "open"
    priority: Priority | None = None
    assignees: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    author: str | None = None
    comments: int = 0
    updated_at: str | None = None


class WorkQuery(BaseModel):
    status: list[Status] = Field(default_factory=list)
    priority: list[Priority] = Field(default_factory=list)
    assignee: str | None = None
    kind: list[Kind] = Field(default_factory=list)
    text: str | None = None
    include_done: bool = False


class WorkChange(BaseModel):
    """What to change on an item. Fields left as None stay as they are."""

    status: Status | None = None
    priority: Priority | None = None
    clear_priority: bool = False
    assignees: list[str] | None = None
    add_labels: list[str] = Field(default_factory=list)
    remove_labels: list[str] = Field(default_factory=list)


class WorkDraft(BaseModel):
    title: str
    body: str
    priority: Priority | None = None
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)


class TrackerError(Exception):
    """The tracker refused or could not be reached."""


class Tracker(Protocol):
    name: str

    async def items(self, repos: list[str], query: WorkQuery) -> list[WorkItem]: ...

    async def update(self, repo: str, number: int, change: WorkChange) -> WorkItem: ...

    async def comment(self, repo: str, number: int, body: str) -> None: ...

    async def create(self, repo: str, draft: WorkDraft) -> WorkItem: ...

    async def people(self, repo: str) -> list[Person]: ...
