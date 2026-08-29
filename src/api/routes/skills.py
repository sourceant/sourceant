"""The skills this machine and its repositories already hold.

People have been teaching their coding agents how work is done here for a
while, and they wrote it down in a folder the agent reads. That folder is a
statement of a team's standards, and nothing else in this product could see it.

This reads it, and only reads it. Skills are files somebody owns and edits with
their editor; a route that wrote them would be a second, worse editor for the
same files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.api.routes.code import find_repository, registered
from src.core.responses import success_response
from src.core.skills import Catalogue, Skill, SkillQuery, sources_for

router = APIRouter()


def catalogue_for(repository: str = "") -> Catalogue:
    root = Path(find_repository(repository).path) if repository else None
    return Catalogue(sources=sources_for(root))


def payload(skill: Skill, full: bool = False) -> dict[str, Any]:
    listed = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "origin": skill.origin,
        "path": skill.path,
    }
    if full:
        listed["body"] = skill.body
        listed["properties"] = dict(skill.properties)
    return listed


@router.get("")
def read_skills(
    repository: str = Query(default=""),
    text: str = Query(default=""),
    origin: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Every skill on hand, machine-wide and this repository's own."""
    if repository and not registered():
        raise HTTPException(status_code=404, detail="No repository is registered")
    result = catalogue_for(repository).search(
        SkillQuery(
            origins=(origin,) if origin else (),
            text=text,
            limit=limit,
        )
    )
    return success_response(
        {
            "skills": [payload(skill) for skill in result.skills],
            "total": result.total,
        }
    )


@router.get("/{skill_id:path}")
def read_skill(skill_id: str, repository: str = Query(default="")):
    """One skill, in full, so a person can read what a check was made against."""
    skill = catalogue_for(repository).get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"No skill called {skill_id}")
    return success_response(payload(skill, full=True))
