from __future__ import annotations

from typing import Mapping, Sequence

from src.core.scope import Scope
from src.core.sql_support import chunked

from .interfaces import Groupable, GroupsRepository
from .models import (
    BREADTH,
    MAX_DEPTH,
    Group,
    GroupContents,
    GroupMember,
    GroupQuery,
    GroupResult,
    GroupRollup,
)


class CheckedGroups:
    """A group store that only files things somebody owns, and can add them up.

    The store underneath knows nothing about requirements, knowledge or
    systems. What each type is, whether an identity is real, and what counting
    it means are answered by whoever owns that kind, so this is where the two
    meet.
    """

    def __init__(
        self,
        repository: GroupsRepository,
        contributors: Sequence[Groupable] = (),
    ) -> None:
        self._groups = repository
        self._contributors = {item.type: item for item in contributors}

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(sorted(self._contributors))

    def place(self, scope: Scope, member: GroupMember) -> None:
        owner = self._contributors.get(member.member_type)
        if owner is None:
            known = ", ".join(self.types) or "nothing"
            raise ValueError(
                f"a {member.member_type} cannot be grouped; this serves {known}"
            )
        if member.member_id not in owner.exists(
            member.member_scope, frozenset({member.member_id})
        ):
            raise ValueError(
                f"{member.member_type} {member.member_id} is not in that scope"
            )
        self._groups.place(scope, member)

    def rollup(
        self, scope: Scope, group_ids: frozenset[str], *, depth: int = MAX_DEPTH
    ) -> tuple[GroupRollup, ...]:
        """What each group adds up to, itself and everything nested in it."""
        answered = []
        for group_id in sorted(group_ids):
            held = self._groups.contents(scope, group_id, depth=depth)
            answered.append(
                GroupRollup(
                    group_id=group_id,
                    counts=self._counts(held),
                    descendants=len(held.groups),
                    truncated=held.truncated,
                )
            )
        return tuple(answered)

    def _counts(self, held: GroupContents) -> Mapping[str, Mapping[str, int]]:
        by_type: dict[str, dict[Scope, set[str]]] = {}
        for member in held.members:
            by_scope = by_type.setdefault(member.member_type, {})
            by_scope.setdefault(member.member_scope, set()).add(member.member_id)

        counts: dict[str, dict[str, int]] = {}
        for type_, by_scope in by_type.items():
            owner = self._contributors.get(type_)
            if owner is None:
                # Filed before whoever owns the type was registered, so there
                # is a count and no summary.
                counts[type_] = {
                    "total": sum(len(ids) for ids in by_scope.values()),
                }
                continue
            summed: dict[str, int] = {}
            for member_scope, ids in by_scope.items():
                for chunk in chunked(ids, 100):
                    for name, value in owner.summarize(
                        member_scope, frozenset(chunk)
                    ).items():
                        summed[name] = summed.get(name, 0) + value
            counts[type_] = summed
        return counts

    def put(self, scope: Scope, group: Group) -> None:
        self._groups.put(scope, group)

    def unfile(
        self,
        scope: Scope,
        group_id: str,
        member_type: str,
        member_id: str,
        member_scope: Scope,
    ) -> bool:
        return self._groups.unfile(
            scope, group_id, member_type, member_id, member_scope
        )

    def remove(self, scope: Scope, group_id: str) -> None:
        self._groups.remove(scope, group_id)

    def search(self, query: GroupQuery) -> GroupResult:
        return self._groups.search(query)

    def members(
        self, scope: Scope, group_ids: frozenset[str]
    ) -> tuple[GroupMember, ...]:
        return self._groups.members(scope, group_ids)

    def filed_under(
        self,
        scope: Scope,
        member_type: str,
        member_ids: frozenset[str],
        member_scope: Scope,
    ) -> Mapping[str, tuple[str, ...]]:
        return self._groups.filed_under(scope, member_type, member_ids, member_scope)

    def ancestry(self, scope: Scope, group_id: str) -> tuple[Group, ...]:
        return self._groups.ancestry(scope, group_id)

    def contents(
        self,
        scope: Scope,
        group_id: str,
        *,
        depth: int = MAX_DEPTH,
        breadth: int = BREADTH,
    ) -> GroupContents:
        return self._groups.contents(scope, group_id, depth=depth, breadth=breadth)
