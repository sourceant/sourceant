from __future__ import annotations

import json
from threading import RLock
from typing import Any, Mapping, Optional

from sqlalchemy import (
    BigInteger,
    Column,
    Connection,
    Engine,
    Index,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    select,
)

from src.core import scopes
from src.core.scope import Scope
from src.core.sql_support import rows_for

from .models import (
    BREADTH,
    MAX_DEPTH,
    Group,
    GroupContents,
    GroupMember,
    GroupQuery,
    GroupResult,
)

metadata = MetaData()

#: Not "groups": GROUPS is reserved in MySQL 8.0, and MySQL is a target.
group_table = Table(
    "object_groups",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("type", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("name", Text, nullable=False),
    Column("parent_id", String(255), nullable=False, default=""),
    Column("external_ref", String(500), nullable=False, default=""),
    Column("properties", Text, nullable=False),
    Index("ix_object_groups_scope_parent", "scope_id", "parent_id"),
    Index("ix_object_groups_scope_type", "scope_id", "type"),
    Index("ix_object_groups_scope_external_ref", "scope_id", "external_ref"),
)

member_table = Table(
    "object_group_members",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("group_id", String(255), primary_key=True),
    Column("member_scope_id", BigInteger, primary_key=True),
    Column("member_type", String(64), primary_key=True),
    Column("member_id", String(255), primary_key=True),
    # Nothing keeps a thing to one group. Each group's own count is exact; a
    # sum across groups is not, since a shared member sits in each of them.
    Index(
        "ix_object_group_members_reverse",
        "member_scope_id",
        "member_type",
        "member_id",
    ),
)


class SQLGroupsRepository:
    """Groups and what they hold, keyed by scope like everything else here.

    It knows nothing about the things it files. Whether a member exists is
    somebody else's answer, asked for one layer up.
    """

    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        if create_schema:
            scopes.ensure(engine)
            metadata.create_all(engine)

    def put(self, scope: Scope, group: Group) -> None:
        with self._lock:
            with self._engine.begin() as connection:
                key = scopes.remembered(connection, scope)
                if group.parent_id:
                    self._parent_can_hold(connection, key, group)
                connection.execute(
                    delete(group_table).where(
                        group_table.c.scope_id == key,
                        group_table.c.id == group.id,
                    )
                )
                connection.execute(
                    group_table.insert().values(
                        scope_id=key,
                        id=group.id,
                        type=group.type,
                        status=group.status,
                        name=group.name,
                        parent_id=group.parent_id,
                        external_ref=group.external_ref,
                        properties=_encode(group.properties),
                    )
                )

    def place(self, scope: Scope, member: GroupMember) -> None:
        with self._lock:
            with self._engine.begin() as connection:
                key = scopes.remembered(connection, scope)
                known = connection.execute(
                    select(group_table.c.id).where(
                        group_table.c.scope_id == key,
                        group_table.c.id == member.group_id,
                    )
                ).first()
                if known is None:
                    raise ValueError("a member needs a group in the same scope")
                member_key = scopes.remembered(connection, member.member_scope)
                # Delete then insert, so filing twice writes one row and the
                # other groups holding it keep theirs.
                connection.execute(
                    delete(member_table).where(
                        member_table.c.scope_id == key,
                        member_table.c.group_id == member.group_id,
                        member_table.c.member_scope_id == member_key,
                        member_table.c.member_type == member.member_type,
                        member_table.c.member_id == member.member_id,
                    )
                )
                connection.execute(
                    member_table.insert().values(
                        scope_id=key,
                        group_id=member.group_id,
                        member_scope_id=member_key,
                        member_type=member.member_type,
                        member_id=member.member_id,
                    )
                )

    def unfile(
        self,
        scope: Scope,
        group_id: str,
        member_type: str,
        member_id: str,
        member_scope: Scope,
    ) -> bool:
        """Take one thing out of one group, leaving the others holding it."""
        key = scopes.known_id(self._engine, scope)
        member_key = scopes.known_id(self._engine, member_scope)
        with self._lock, self._engine.begin() as connection:
            result = connection.execute(
                delete(member_table).where(
                    member_table.c.scope_id == key,
                    member_table.c.group_id == group_id,
                    member_table.c.member_scope_id == member_key,
                    member_table.c.member_type == member_type,
                    member_table.c.member_id == member_id,
                )
            )
            return result.rowcount > 0

    def remove(self, scope: Scope, group_id: str) -> None:
        key = scopes.known_id(self._engine, scope)
        with self._lock:
            with self._engine.begin() as connection:
                held = connection.execute(
                    select(func.count())
                    .select_from(member_table)
                    .where(
                        member_table.c.scope_id == key,
                        member_table.c.group_id == group_id,
                    )
                ).scalar_one()
                nested = connection.execute(
                    select(func.count())
                    .select_from(group_table)
                    .where(
                        group_table.c.scope_id == key,
                        group_table.c.parent_id == group_id,
                    )
                ).scalar_one()
                if held or nested:
                    raise ValueError(
                        "a group that still holds things cannot be removed"
                    )
                connection.execute(
                    delete(group_table).where(
                        group_table.c.scope_id == key,
                        group_table.c.id == group_id,
                    )
                )

    def search(self, query: GroupQuery) -> GroupResult:
        key = scopes.known_id(self._engine, query.scope)
        statement = select(group_table).where(group_table.c.scope_id == key)
        if query.ids:
            statement = statement.where(group_table.c.id.in_(sorted(query.ids)))
        if query.types:
            statement = statement.where(group_table.c.type.in_(sorted(query.types)))
        if query.statuses:
            statement = statement.where(
                group_table.c.status.in_(sorted(query.statuses))
            )
        if query.parent_ids:
            statement = statement.where(
                group_table.c.parent_id.in_(sorted(query.parent_ids))
            )
        with self._engine.connect() as connection:
            total = connection.execute(
                select(func.count()).select_from(statement.subquery())
            ).scalar_one()
            rows = list(
                connection.execute(
                    statement.order_by(group_table.c.id)
                    .limit(query.limit)
                    .offset(query.offset)
                ).mappings()
            )
        items = tuple(_group_from_row(row) for row in rows)
        return GroupResult(
            items=items,
            total=total,
            has_more=query.offset + len(items) < total,
        )

    def members(
        self, scope: Scope, group_ids: frozenset[str]
    ) -> tuple[GroupMember, ...]:
        key = scopes.known_id(self._engine, scope)
        held = scopes.scope_table.alias("member_scopes")
        statement = select(member_table, held.c.qualifiers).join(
            held, member_table.c.member_scope_id == held.c.id
        )
        with self._engine.connect() as connection:
            if not group_ids:
                rows = list(
                    connection.execute(
                        statement.where(member_table.c.scope_id == key)
                    ).mappings()
                )
            else:
                rows = rows_for(
                    group_ids,
                    lambda chunk: connection.execute(
                        statement.where(
                            member_table.c.scope_id == key,
                            member_table.c.group_id.in_(chunk),
                        )
                    ).mappings(),
                )
        found = tuple(_member_from_row(row) for row in rows)
        return tuple(
            sorted(
                found,
                key=lambda one: (one.group_id, one.member_type, one.member_id),
            )
        )

    def filed_under(
        self,
        scope: Scope,
        member_type: str,
        member_ids: frozenset[str],
        member_scope: Scope,
    ) -> Mapping[str, tuple[str, ...]]:
        """Which groups each of these is in, for the ones that are in any."""
        if not member_ids:
            return {}
        key = scopes.known_id(self._engine, scope)
        member_key = scopes.known_id(self._engine, member_scope)
        with self._engine.connect() as connection:
            rows = rows_for(
                member_ids,
                lambda chunk: connection.execute(
                    select(member_table.c.member_id, member_table.c.group_id).where(
                        member_table.c.scope_id == key,
                        member_table.c.member_scope_id == member_key,
                        member_table.c.member_type == member_type,
                        member_table.c.member_id.in_(chunk),
                    )
                ),
            )
        found: dict[str, set[str]] = {}
        for member_id, group_id in rows:
            found.setdefault(member_id, set()).add(group_id)
        return {member_id: tuple(sorted(ids)) for member_id, ids in found.items()}

    def ancestry(self, scope: Scope, group_id: str) -> tuple[Group, ...]:
        """The path down to this group, outermost first, itself last."""
        key = scopes.known_id(self._engine, scope)
        with self._engine.connect() as connection:
            path: list[Group] = []
            current: Optional[str] = group_id
            seen: set[str] = set()
            while current and current not in seen:
                seen.add(current)
                row = connection.execute(
                    select(group_table).where(
                        group_table.c.scope_id == key,
                        group_table.c.id == current,
                    )
                ).mappings()
                found = row.first()
                if found is None:
                    break
                group = _group_from_row(found)
                path.append(group)
                current = group.parent_id or None
        return tuple(reversed(path))

    def contents(
        self,
        scope: Scope,
        group_id: str,
        *,
        depth: int = MAX_DEPTH,
        breadth: int = BREADTH,
    ) -> GroupContents:
        """Walk down from one group, a level at a time.

        A level at a time rather than in one statement, because the nesting has
        no fixed depth and a recursive query is not the same on every database
        this runs on.

        Bounded both ways: how deep it descends, and how many groups and members
        it answers with.
        """
        key = scopes.known_id(self._engine, scope)
        nested: list[str] = []
        seen = {group_id}
        frontier = [group_id]
        truncated = False
        with self._engine.connect() as connection:
            for _ in range(max(1, depth)):
                if not frontier:
                    break
                children = [
                    row[0]
                    for row in rows_for(
                        frontier,
                        lambda chunk: connection.execute(
                            select(group_table.c.id).where(
                                group_table.c.scope_id == key,
                                group_table.c.parent_id.in_(chunk),
                            )
                        ),
                    )
                ]
                frontier = []
                for child in children:
                    if child in seen:
                        continue
                    seen.add(child)
                    nested.append(child)
                    frontier.append(child)
            truncated = bool(frontier)
        held = tuple(sorted(nested))
        if len(held) > breadth:
            held, truncated = held[:breadth], True
        members = self.members(scope, frozenset({group_id, *held}))
        if len(members) > breadth:
            members, truncated = members[:breadth], True
        return GroupContents(groups=held, members=members, truncated=truncated)

    def _parent_can_hold(self, connection: Connection, key: int, group: Group) -> None:
        parent = connection.execute(
            select(group_table.c.id).where(
                group_table.c.scope_id == key,
                group_table.c.id == group.parent_id,
            )
        ).first()
        if parent is None:
            raise ValueError("a group's parent has to be in the same scope")
        # Bounded by the chain rather than by a depth, with seen guarding
        # against a cycle already stored.
        current: Optional[str] = group.parent_id
        seen: set[str] = set()
        while current and current not in seen:
            if current == group.id:
                raise ValueError("group nesting cannot contain a cycle")
            seen.add(current)
            row = connection.execute(
                select(group_table.c.parent_id).where(
                    group_table.c.scope_id == key,
                    group_table.c.id == current,
                )
            ).first()
            current = row[0] if row is not None else None


def _group_from_row(row: Mapping[str, Any]) -> Group:
    return Group(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        name=row["name"],
        parent_id=row["parent_id"],
        external_ref=row["external_ref"],
        properties=json.loads(row["properties"]),
    )


def _member_from_row(row: Mapping[str, Any]) -> GroupMember:
    return GroupMember(
        group_id=row["group_id"],
        member_type=row["member_type"],
        member_id=row["member_id"],
        member_scope=scopes.read(row["qualifiers"]),
    )


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
