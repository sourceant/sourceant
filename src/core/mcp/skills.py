from __future__ import annotations

import json
from typing import Any

from src.core.environment import LOCAL
from src.core.scope import Scope
from src.core.skills import Skill, SkillLibrary, skill_from_markdown


def add_skill_tools(server, services, resolve_scope, surface, library=None):
    if library is None:
        try:
            library = services.resolve(SkillLibrary)
        except LookupError:
            return

    def location(scope):
        active = resolve_scope(Scope.from_mapping(scope))
        workspace = active.get("workspace")
        if surface is not None and surface.reaches_checkout:
            workspace = LOCAL
        if not workspace:
            raise ValueError("A workspace is required")
        return workspace, active.get("repository") or ""

    def destination(scope):
        workspace, repository = location(scope)
        target = "repository" if repository else "workspace"
        if not repository and surface is not None and surface.reaches_checkout:
            target = "global"
        return workspace, repository, target

    from src.core.mcp.skill_prompts import add_skill_prompts

    add_skill_prompts(server, library, location)

    def details(skill, max_characters):
        extra = {
            "content": {**skill.content, "instructions": skill.body},
            "metadata": dict(skill.metadata),
            "properties": dict(skill.properties),
            "paths": list(skill.paths),
        }
        truncated = len(json.dumps(extra)) > max_characters
        body_truncated = len(skill.body) > max_characters
        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description[:2000],
            "kind": skill.kind,
            "origin": skill.origin,
            "automatic": skill.automatic,
            "body": skill.body[:max_characters],
            "body_truncated": body_truncated,
            "truncated": truncated or body_truncated or len(skill.description) > 2000,
            **{key: None if truncated else value for key, value in extra.items()},
            "content_truncated": truncated,
            "details_truncated": truncated,
            "applications": {
                **({"review": skill.reviews} if skill.reviews is not None else {}),
                **skill.applications,
            },
        }

    @server.tool(
        name="search_skills",
        description="Find available workspace and repository skills without installing them.",
        structured_output=True,
    )
    def search_skills(
        scope: dict[str, str],
        text: str = "",
        limit: int = 20,
        offset: int = 0,
        kind: str = "",
        purpose: str = "",
    ) -> dict[str, Any]:
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError(
                "limit must be between 1 and 50; offset must be nonnegative"
            )
        if len(text) > 500:
            raise ValueError("search text must be at most 500 characters")
        workspace, repository = location(scope)
        term = text.strip().casefold()
        found = sorted(
            (
                skill
                for skill in library.all(workspace, repository)
                if term in f"{skill.id} {skill.name} {skill.description}".casefold()
                and (not kind or skill.kind == kind)
                and (not purpose or skill.applies_to(purpose) is True)
            ),
            key=lambda skill: (skill.id, skill.origin),
        )
        return {
            "skills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description[:2000],
                    "origin": skill.origin,
                    "automatic": skill.automatic,
                    "kind": skill.kind,
                    "applications": {
                        **(
                            {"review": skill.reviews}
                            if skill.reviews is not None
                            else {}
                        ),
                        **skill.applications,
                    },
                }
                for skill in found[offset : offset + limit]
            ],
            "total": len(found),
            "has_more": offset + limit < len(found),
        }

    @server.tool(
        name="get_skill",
        description="Read a skill's instructions. Reading does not install or execute it.",
        structured_output=True,
    )
    def get_skill(
        scope: dict[str, str], id: str, max_characters: int = 20000
    ) -> dict[str, Any]:
        if not 1 <= max_characters <= 100000:
            raise ValueError("max_characters must be between 1 and 100000")
        workspace, repository = location(scope)
        skill = library.one(workspace, id, repository)
        if skill is None:
            raise ValueError("Skill not found in this scope")
        return details(skill, max_characters)

    @server.tool(
        name="save_skill",
        description="Create or replace an owned workspace or repository skill. Does not execute it. Supply a portable document or structured fields.",
        structured_output=True,
    )
    def save_skill(
        scope: dict[str, str],
        id: str,
        name: str = "",
        description: str = "",
        content: dict[str, Any] | None = None,
        body: str = "",
        kind: str = "guidance",
        applications: dict[str, bool] | None = None,
        paths: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        automatic: bool = True,
        document: str | None = None,
    ) -> dict[str, Any]:
        workspace, repository, target = destination(scope)
        if document is not None:
            if len(document.encode()) > 100000:
                raise ValueError("Skill document exceeds 100000 bytes")
            skill = skill_from_markdown(document, id)
        else:
            content = dict(content or {})
            instructions = content.get("instructions", body)
            if not isinstance(instructions, str) or not instructions.strip():
                raise ValueError("Content needs text instructions")
            if body and body != instructions:
                raise ValueError("Body and content instructions must agree")
            content["instructions"] = instructions
            extra = dict(metadata or {})
            own = extra.get("sourceant", {})
            if not isinstance(own, dict):
                raise ValueError("SourceAnt metadata must be an object")
            extra["sourceant"] = {**own, "type": kind}
            skill = Skill(
                id,
                name,
                description,
                instructions,
                paths=tuple(paths or ()),
                metadata=extra,
                properties=properties or {},
                automatic=automatic,
                content=content,
                applications=applications or {},
            )
        if not skill.description.strip() or not skill.body.strip():
            raise ValueError("A skill needs a description and instructions")
        if (
            len(skill.name) > 200
            or len(skill.description) > 2000
            or len(skill.body) > 20000
        ):
            raise ValueError("Skill text exceeds the storage limits")
        if not kind.strip() or len(skill.kind) > 128 or len(skill.paths) > 100:
            raise ValueError("Skill kind or paths exceed the storage limits")
        from dataclasses import asdict

        if len(json.dumps(asdict(skill), allow_nan=False).encode()) > 100000:
            raise ValueError("Skill exceeds 100000 bytes")
        saved = library.write(workspace, skill, scope=target, repository=repository)
        return details(saved, 100000)

    @server.tool(
        name="delete_skill",
        description="Delete an owned skill from the resolved workspace or repository scope. System skills remain read-only.",
        structured_output=True,
    )
    def delete_skill(scope: dict[str, str], id: str) -> dict[str, Any]:
        workspace, repository, target = destination(scope)
        return {
            "deleted": library.forget(
                workspace, id, scope=target, repository=repository
            )
        }
