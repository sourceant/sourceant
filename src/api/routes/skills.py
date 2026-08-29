"""The skills this machine and its repositories already hold.

People have been teaching their coding agents how work is done here for a
while, and they wrote it down in a folder the agent reads. That folder is a
statement of a team's standards, and nothing else in this product could see it.

What a person keeps in their own agent folders is read and never written: those
files are theirs, are often a link into a checkout of their own, and writing
through one would edit somebody else's repository without saying so.

A repository's own rules are a different matter. Most of what a team knows is in
somebody's head rather than in any folder, and asking them to go and write a
file by hand is where it stays. Those are written here, into the repository the
rule is about, so the rest of the team gets it by pulling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, registered, require_local
from src.core.responses import success_response
from src.core.skills import (
    Catalogue,
    Skill,
    SkillQuery,
    SkillWriteError,
    remove_skill,
    sources_for,
    write_skill,
)

router = APIRouter()

THEIRS = (
    "That rule is one of yours, kept wherever your coding agent keeps it. It is "
    "read here and never written. Save it into this repository to change it here."
)


class SkillInput(BaseModel):
    repository: str = Field(...)
    id: str = Field(...)
    name: str = Field(default="")
    description: str = Field(default="")
    body: str = Field(default="")


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


@router.put("", dependencies=[Depends(require_local)])
def record_skill(body: SkillInput):
    """State a rule in the repository it is about."""
    entry = find_repository(body.repository)
    try:
        written = write_skill(
            Path(entry.path),
            Skill(
                id=body.id,
                name=body.name or body.id,
                description=body.description,
                body=body.body,
            ),
        )
    except SkillWriteError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return success_response(payload(written, full=True))


@router.delete("", dependencies=[Depends(require_local)])
def forget_skill(
    repository: str = Query(...),
    id: str = Query(...),
):
    """Forget a rule this repository stated.

    Only its own. A rule somebody keeps in their agent folder is theirs, and
    this is not the place it gets deleted from.
    """
    entry = find_repository(repository)
    try:
        gone = remove_skill(Path(entry.path), id)
    except SkillWriteError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not gone:
        existing = catalogue_for(repository).get(id)
        raise HTTPException(
            status_code=404 if existing is None else 400,
            detail=THEIRS if existing is not None else f"No skill called {id}",
        )
    return success_response({"id": id})


@router.get("/{skill_id:path}")
def read_skill(skill_id: str, repository: str = Query(default="")):
    """One skill, in full, so a person can read what a check was made against."""
    skill = catalogue_for(repository).get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"No skill called {skill_id}")
    return success_response(payload(skill, full=True))
