from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    BREADTH,
    Group,
    GroupContents,
    GroupMember,
    GroupQuery,
    GroupResult,
    GroupRollup,
    Grouping,
    MAX_DEPTH,
)


@runtime_checkable
class GroupsReader(Protocol):
    def search(self, query: GroupQuery) -> GroupResult: ...

    def members(
        self, scope: Scope, group_ids: frozenset[str]
    ) -> tuple[GroupMember, ...]: ...

    def filed_under(
        self,
        scope: Scope,
        member_type: str,
        member_ids: frozenset[str],
        member_scope: Scope,
    ) -> Mapping[str, tuple[str, ...]]: ...

    def ancestry(self, scope: Scope, group_id: str) -> tuple[Group, ...]: ...

    def contents(
        self,
        scope: Scope,
        group_id: str,
        *,
        depth: int = MAX_DEPTH,
        breadth: int = BREADTH,
    ) -> GroupContents: ...


@runtime_checkable
class GroupsWriter(Protocol):
    def put(self, scope: Scope, group: Group) -> None: ...

    def place(self, scope: Scope, member: GroupMember) -> None: ...

    def unfile(
        self,
        scope: Scope,
        group_id: str,
        member_type: str,
        member_id: str,
        member_scope: Scope,
    ) -> bool: ...

    def remove(self, scope: Scope, group_id: str) -> None: ...


@runtime_checkable
class GroupsRepository(GroupsReader, GroupsWriter, Protocol):
    pass


@runtime_checkable
class GroupRollupReader(Protocol):
    """Adding a group up, which needs whoever owns the things it holds.

    Separate from the reader because a store that only files things is still a
    usable store. A caller asks with isinstance.
    """

    def rollup(
        self, scope: Scope, group_ids: frozenset[str], *, depth: int = MAX_DEPTH
    ) -> tuple[GroupRollup, ...]: ...


@runtime_checkable
class Groupable(Protocol):
    """A type of thing that can be put in a group.

    Whoever owns that type implements this and contributes it, which is how a
    group can hold requirements today and knowledge tomorrow without the group
    store learning what either of them is. ``summarize`` answers in whatever
    numbers that type is measured by.
    """

    @property
    def type(self) -> str: ...

    def exists(self, scope: Scope, ids: frozenset[str]) -> tuple[str, ...]: ...

    def summarize(self, scope: Scope, ids: frozenset[str]) -> Mapping[str, int]: ...


@runtime_checkable
class GroupSource(Protocol):
    """Grouping a team already keeps somewhere else.

    A milestone, an epic, a parent issue. Implementations read from a tracker
    and hand back what they found. Writing it down stays with the caller.
    """

    def sync(self, scope: Scope) -> tuple[Grouping, ...]: ...
