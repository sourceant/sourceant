from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope

#: How deep nesting may go before a walk stops descending. A group has one
#: parent and no cycles, so this is depth rather than a guard against looping.
MAX_DEPTH = 20

#: How much one walk answers with, nested groups and members alike. A walk that
#: runs past it reports that it was cut.
BREADTH = 500


@dataclass(frozen=True)
class Group:
    """Something things belong to: a project, a feature, a task, a release.

    What sort of thing it is stands in ``type``, and nothing here decides that
    vocabulary. A description, an owner and dates ride in ``properties``.
    """

    id: str
    type: str
    status: str
    name: str
    parent_id: str = ""
    external_ref: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value, limit in (
            ("id", self.id, 255),
            ("type", self.type, 255),
            ("status", self.status, 255),
            ("name", self.name, 0),
            ("parent id", self.parent_id, 255),
            ("external ref", self.external_ref, 500),
        ):
            if not value and name not in ("parent id", "external ref"):
                raise ValueError(f"group {name} must not be empty")
            if limit and len(value) > limit:
                raise ValueError(
                    f"group {name} must contain at most {limit} characters"
                )
        if self.parent_id == self.id:
            raise ValueError("a group cannot hold itself")


@dataclass(frozen=True)
class GroupMember:
    """One thing filed under a group.

    The member names its own scope, because a group is filed where its surface
    puts it and the things it holds are filed where theirs do. A workspace's
    feature holding requirements from four repositories is that difference.
    """

    group_id: str
    member_type: str
    member_id: str
    member_scope: Scope

    def __post_init__(self) -> None:
        if not self.group_id or not self.member_type or not self.member_id:
            raise ValueError("a member needs a group, a type, and an identity")
        if not self.member_scope.values:
            raise ValueError("a member needs a scope of its own")
        for name, value, limit in (
            ("group", self.group_id, 255),
            ("type", self.member_type, 64),
            ("identity", self.member_id, 500),
        ):
            if len(value) > limit:
                raise ValueError(
                    f"a member's {name} must contain at most {limit} characters"
                )


@dataclass(frozen=True)
class GroupQuery:
    """Groups to answer with. An empty ``parent_ids`` asks about every level;
    ``frozenset({""})`` asks for the outermost ones only."""

    scope: Scope
    ids: frozenset[str] = field(default_factory=frozenset)
    types: frozenset[str] = field(default_factory=frozenset)
    statuses: frozenset[str] = field(default_factory=frozenset)
    parent_ids: frozenset[str] = field(default_factory=frozenset)
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.offset < 0:
            raise ValueError("offset must not be negative")
        for name, values in (
            ("ids", self.ids),
            ("types", self.types),
            ("statuses", self.statuses),
            ("parent_ids", self.parent_ids),
        ):
            if len(values) > 100:
                raise ValueError(f"{name} must contain at most 100 values")


@dataclass(frozen=True)
class GroupResult:
    items: tuple[Group, ...]
    total: int
    has_more: bool


@dataclass(frozen=True)
class GroupContents:
    """Everything one group holds, however deeply it is nested."""

    #: Groups nested inside it, at any depth.
    groups: tuple[str, ...] = ()
    #: What it and those hold.
    members: tuple[GroupMember, ...] = ()
    #: Whether the walk stopped before it ran out of nesting or ran past what
    #: one answer carries. A caller summing a truncated answer would report less
    #: than is there, so it has to know.
    truncated: bool = False


@dataclass(frozen=True)
class GroupRollup:
    """What a group adds up to, one summary per type of thing it holds.

    The counts are whatever each type answers with: how much of a requirement
    is done and how much of a system is drawn are not the same numbers.
    """

    group_id: str
    counts: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    descendants: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class Grouping:
    """A group and what belongs in it, as something elsewhere already has it."""

    group: Group
    members: tuple[GroupMember, ...] = ()
