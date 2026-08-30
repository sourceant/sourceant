"""The skills this machine and its repositories already hold.

People have been teaching their coding agents how work is done here for a
while, and they wrote it down in a folder the agent reads. That folder is a
statement of a team's standards, and nothing else in this product could see it.

What a person keeps in their own agent folders is read and never written: those
files are theirs, are often a link into a checkout of their own, and writing
through one would edit somebody else's repository without saying so.

What this product keeps is a different matter. Most of what a team knows is in
somebody's head rather than in any folder, and asking them to go and write a
file by hand is where it stays. Those are written here, beside the index rather
than inside anybody's repository: a folder appearing in somebody's checkout
because a tool was opened turns up in their `git status` and in a review nobody
asked for. Which repository one is for is recorded rather than implied by where
the file sits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, registered, require_local
from src.core.responses import success_response
from src.core.skills import (
    NAMESPACE,
    REVIEW,
    Catalogue,
    Skill,
    SkillQuery,
    SkillWriteError,
    global_skills,
    remove_skill,
    repository_skills,
    sources_for,
    write_skill,
)

router = APIRouter()

THEIRS = (
    "That skill is one of yours, kept wherever your coding agent keeps it, or "
    "committed to the repository by your team. It is read here and never "
    "written. Save your own copy to change it here."
)

# Who a skill is for. One project, or everything somebody works on. Both are
# kept beside the index; the difference is which is read where, not which
# folder on somebody else's checkout got written to.
REPOSITORY = "repository"
GLOBAL = "global"
SCOPES = (REPOSITORY, GLOBAL)


class SkillInput(BaseModel):
    id: str = Field(...)
    # Empty for a skill that applies everywhere rather than to one project.
    repository: str = Field(default="")
    scope: str = Field(default=REPOSITORY)
    name: str = Field(default="")
    description: str = Field(default="")
    body: str = Field(default="")
    # Globs naming the files this is about, so it is picked on a statement
    # rather than on how its description happens to be worded.
    paths: list[str] = Field(default_factory=list)
    # Whether it belongs in a review at all. Null leaves that unsaid, which is
    # where most skills are and is not the same as saying no.
    reviews: bool | None = Field(default=None)


def where(scope: str, repository: str) -> Path:
    """Where a skill for this scope is kept.

    Never inside the repository. A repository belongs to the team that owns it,
    and a folder appearing in it because a tool was opened is that tool helping
    itself to somebody else's checkout.
    """
    if scope not in SCOPES:
        raise HTTPException(
            status_code=400, detail=f"scope must be one of {', '.join(SCOPES)}"
        )
    if scope == GLOBAL:
        return global_skills()
    if not repository:
        raise HTTPException(
            status_code=400, detail="Name the repository this belongs to"
        )
    # Checked, so a skill cannot be filed against a repository nobody covers.
    find_repository(repository)
    return repository_skills(repository)


def elsewhere() -> tuple[str, ...]:
    """The folders somebody told this machine to look in as well."""
    from src.api.routes.local_settings import WHOEVER_IS_HERE
    from src.core.settings.resolver import resolve

    try:
        named = str(resolve("skills.paths", user=WHOEVER_IS_HERE).value or "")
    except Exception:  # noqa: BLE001 - a store that cannot answer is no folders
        return ()
    return tuple(line.strip() for line in named.splitlines() if line.strip())


def catalogue_for(repository: str = "") -> Catalogue:
    root = Path(find_repository(repository).path) if repository else None
    ours = [(global_skills(), GLOBAL)]
    if repository:
        ours.append((repository_skills(repository), REPOSITORY))
    return Catalogue(sources=sources_for(root, elsewhere(), ours))


def payload(skill: Skill, full: bool = False) -> dict[str, Any]:
    listed = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "origin": skill.origin,
        "path": skill.path,
        "paths": list(skill.paths),
        "reviews": skill.reviews,
        "automatic": skill.automatic,
    }
    if full:
        listed["body"] = skill.body
        listed["metadata"] = dict(skill.metadata)
        listed["properties"] = dict(skill.properties)
    return listed


@router.get("")
def read_skills(
    repository: str = Query(default=""),
    text: str = Query(default=""),
    origin: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Every skill on hand: yours, your agents', and this repository's."""
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
    """Write a skill down, for one repository or for everything."""
    root = where(body.scope, body.repository)
    # Kept where the format sets a map aside for it, namespaced as the spec
    # asks, so a skill carrying it stays readable by everything else.
    metadata = {} if body.reviews is None else {NAMESPACE: {REVIEW: body.reviews}}
    try:
        written = write_skill(
            root,
            Skill(
                id=body.id,
                name=body.name or body.id,
                description=body.description,
                body=body.body,
                paths=tuple(body.paths),
                metadata=metadata,
            ),
            origin=body.scope,
        )
    except SkillWriteError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return success_response(payload(written, full=True))


@router.delete("", dependencies=[Depends(require_local)])
def forget_skill(
    id: str = Query(...),
    repository: str = Query(default=""),
    scope: str = Query(default=REPOSITORY),
):
    """Forget a skill written here.

    Only one of ours. A skill somebody keeps in their coding agent's folder is
    theirs, and this is not the place it gets deleted from.
    """
    root = where(scope, repository)
    try:
        gone = remove_skill(root, id)
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
