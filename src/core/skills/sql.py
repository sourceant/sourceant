from __future__ import annotations

import json
from dataclasses import asdict, replace

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.dialects import mysql, postgresql, sqlite

from src.core import scopes
from src.core.scope import Scope

from .models import NAMESPACE, REVIEW, Skill, SkillScope, SkillType
from .writing import SkillWriteError, _checked

metadata = MetaData()
skill_table = Table(
    "skills",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("description", Text, nullable=False),
    Column("content", JSON, nullable=False),
    Column("scope", String(32), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("reviews", Boolean, nullable=True),
    Column("automatic", Boolean, nullable=False),
    Column("paths", JSON, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("properties", JSON, nullable=False),
    Column("deleted", Boolean, nullable=False),
)


class SQLSkillLibrary:
    def __init__(self, engine, *, create_schema=False):
        self.engine = engine
        if create_schema:
            scopes.ensure(engine)
            metadata.create_all(engine)

    @staticmethod
    def scope(workspace, scope, repository=""):
        if not workspace:
            raise SkillWriteError("A workspace is required")
        if scope == SkillScope.WORKSPACE:
            return Scope.from_mapping({"workspace": workspace})
        if scope == SkillScope.REPOSITORY and repository:
            return Scope.from_mapping(
                {"workspace": workspace, "repository": repository}
            )
        raise SkillWriteError("Choose a workspace or repository skill scope")

    def entries(self, workspace, *, scope, repository="", identifier=None):
        target = self.scope(workspace, scope, repository)
        with self.engine.connect() as connection:
            key = scopes.known(connection, target)
            if key is None:
                return {}
            query = select(skill_table).where(skill_table.c.scope_id == key)
            if identifier is not None:
                query = query.where(skill_table.c.id == identifier)
            rows = connection.execute(query).mappings()
            found = {}
            for row in rows:
                if row["deleted"]:
                    found[row["id"]] = None
                else:
                    extra = dict(row["metadata"])
                    if (
                        isinstance(extra.get(NAMESPACE), dict)
                        or row["kind"] != SkillType.GUIDANCE.value
                        or row["reviews"] is not None
                    ):
                        own = (
                            dict(extra[NAMESPACE])
                            if isinstance(extra.get(NAMESPACE), dict)
                            else {}
                        )
                        own["type"] = row["kind"]
                        if row["reviews"] is not None:
                            own[REVIEW] = row["reviews"]
                        extra[NAMESPACE] = own
                    found[row["id"]] = Skill(
                        row["id"],
                        row["name"],
                        row["description"],
                        row["content"]["instructions"],
                        origin=row["scope"],
                        paths=tuple(row["paths"]),
                        metadata=extra,
                        automatic=row["automatic"],
                        properties=row["properties"],
                        content=row["content"],
                    )
            return found

    def all(self, workspace, repository=""):
        found = self.entries(workspace, scope=SkillScope.WORKSPACE)
        if repository:
            found.update(
                {
                    key: value
                    for key, value in self.entries(
                        workspace, scope=SkillScope.REPOSITORY, repository=repository
                    ).items()
                    if value is not None
                }
            )
        return tuple(found[key] for key in sorted(found) if found[key] is not None)

    def one(self, workspace, identifier, repository=""):
        if repository:
            found = self.entries(
                workspace,
                scope=SkillScope.REPOSITORY,
                repository=repository,
                identifier=identifier,
            ).get(identifier)
            if found is not None:
                return found
        return self.entries(
            workspace, scope=SkillScope.WORKSPACE, identifier=identifier
        ).get(identifier)

    def _put(self, target, identifier, fields):
        with self.engine.begin() as connection:
            key = scopes.remembered(connection, target)
            values = dict(scope_id=key, id=identifier, **fields)
            dialect = connection.dialect.name
            if dialect == "mysql":
                statement = mysql.insert(skill_table).values(**values)
                statement = statement.on_duplicate_key_update(**fields)
            elif dialect in {"sqlite", "postgresql"}:
                insert = sqlite.insert if dialect == "sqlite" else postgresql.insert
                statement = insert(skill_table).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["scope_id", "id"],
                    set_=fields,
                )
            else:
                raise SkillWriteError("Unsupported skills database")
            connection.execute(statement)

    def write(self, workspace, skill, *, scope, repository=""):
        target = self.scope(workspace, scope, repository)
        kept = replace(
            skill, id=_checked(skill.id), path="", origin=SkillScope(scope).value
        )
        if not kept.description.strip() or not kept.body.strip():
            raise SkillWriteError("A skill needs a description and instructions")
        if len(kept.name) > 200:
            raise SkillWriteError("Skill name exceeds 200 characters")
        content = dict(kept.content)
        if "instructions" in content and content["instructions"] != kept.body:
            raise SkillWriteError("Body and content instructions must agree")
        content["instructions"] = kept.body
        try:
            document = json.dumps(asdict(kept), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise SkillWriteError(
                "Skill metadata must contain JSON-compatible values"
            ) from error
        if len(document.encode()) > 100_000:
            raise SkillWriteError("Skill exceeds the storage limit")
        extra = dict(kept.metadata)
        if isinstance(extra.get(NAMESPACE), dict):
            own = dict(extra[NAMESPACE])
            own.pop("type", None)
            own.pop(REVIEW, None)
            extra[NAMESPACE] = own
        self._put(
            target,
            kept.id,
            {
                "name": kept.name,
                "description": kept.description,
                "content": content,
                "scope": kept.origin,
                "kind": kept.kind.value,
                "reviews": kept.reviews,
                "automatic": kept.automatic,
                "paths": list(kept.paths),
                "metadata": extra,
                "properties": dict(kept.properties),
                "deleted": False,
            },
        )
        return replace(kept, content=content)

    def hide(self, workspace, identifier, *, scope, repository=""):
        self._put(
            self.scope(workspace, scope, repository),
            _checked(identifier),
            {
                "name": "",
                "description": "",
                "content": {},
                "scope": SkillScope(scope).value,
                "kind": SkillType.GUIDANCE.value,
                "reviews": None,
                "automatic": False,
                "paths": [],
                "metadata": {},
                "properties": {},
                "deleted": True,
            },
        )

    def forget(self, workspace, identifier, *, scope, repository=""):
        identifier = _checked(identifier)
        if (
            self.entries(workspace, scope=scope, repository=repository).get(identifier)
            is None
        ):
            return False
        self.hide(workspace, identifier, scope=scope, repository=repository)
        return True
