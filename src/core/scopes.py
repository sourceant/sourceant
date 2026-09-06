"""A scope, recorded once, so that what it qualifies points at it by number.

A scope says what something is about, and it grows: a repository, and a
revision as well once a review is reading one. Written into every row it
qualified, it made keys wide enough that MySQL would not build them, and wider
again whenever a scope gained a qualifier. Recorded here it is one row and one
number, and a key carrying it is eight bytes whatever the scope says.
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Column,
    Connection,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    select,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError

from src.core.scope import Scope

metadata = MetaData()

#: Compared byte for byte. MySQL's default collation ignores case, which would
#: make one scope out of two repositories whose names differ only by it, and the
#: unique column below would hand the second one the first one's number.
QUALIFIERS = String(500).with_variant(
    mysql.VARCHAR(500, collation="utf8mb4_bin"), "mysql"
)

#: The only place a scope is written out in full. It sits alone in its key, so
#: its width is nobody else's problem.
scope_table = Table(
    "scopes",
    metadata,
    # SQLite only counts up a column declared INTEGER, so the wider type is
    # asked for everywhere else and given up there.
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("qualifiers", QUALIFIERS, nullable=False, unique=True),
)


def written(scope: Scope) -> str:
    """A scope as one string, the form it is stored and matched by."""
    return json.dumps(scope.values, separators=(",", ":"))


def read(value: str) -> Scope:
    """The scope a stored string stands for."""
    return Scope(tuple(tuple(item) for item in json.loads(value)))


def known(connection: Connection, scope: Scope) -> Optional[int]:
    """The number this scope goes by, or None if it has never been seen."""
    return connection.execute(
        select(scope_table.c.id).where(scope_table.c.qualifiers == written(scope))
    ).scalar()


def remembered(connection: Connection, scope: Scope) -> int:
    """The number this scope goes by, recording it the first time it is seen."""
    value = written(scope)
    found = connection.execute(
        select(scope_table.c.id).where(scope_table.c.qualifiers == value)
    ).scalar()
    if found is not None:
        return found
    try:
        with connection.begin_nested():
            connection.execute(scope_table.insert().values(qualifiers=value))
    except IntegrityError:
        # Two writers reaching a scope for the first time at once. The column
        # decides which of them wrote it, and both want the same answer. It
        # compares byte for byte, so this is the only way to get here.
        pass
    return connection.execute(
        select(scope_table.c.id).where(scope_table.c.qualifiers == value)
    ).scalar()


def known_id(engine: Engine, scope: Scope) -> Optional[int]:
    """The scope's number, or None when nothing has ever been in it.

    Reads ask after a scope rather than record one, so querying a repository
    nobody has written about leaves nothing behind.
    """
    with engine.connect() as connection:
        return known(connection, scope)


def with_scope(table: Table):
    """Rows with the scope they belong to spelled out again."""
    return select(table, scope_table.c.qualifiers).join(
        scope_table, table.c.scope_id == scope_table.c.id
    )


def ensure(engine: Engine) -> None:
    """Create the table, for stores that build their own schema."""
    metadata.create_all(engine)
