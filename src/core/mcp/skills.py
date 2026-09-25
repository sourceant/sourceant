from __future__ import annotations

import json
from typing import Any

from src.core.environment import LOCAL
from src.core.scope import Scope
from src.core.skills import SkillLibrary


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

    @server.tool(
        name="search_skills",
        description="Find available workspace and repository skills without installing them.",
        structured_output=True,
    )
    def search_skills(
        scope: dict[str, str], text: str = "", limit: int = 20, offset: int = 0
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
        content = {**skill.content, "instructions": skill.body}
        content_truncated = len(json.dumps(content)) > max_characters
        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description[:2000],
            "origin": skill.origin,
            "automatic": skill.automatic,
            "body": skill.body[:max_characters],
            "truncated": len(skill.body) > max_characters,
            "content": None if content_truncated else content,
            "content_truncated": content_truncated,
        }
