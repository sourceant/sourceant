"""Reviewing work before anybody else sees it.

The hosted review happens after a pull request exists, which is after the author
has decided the work is finished and asked other people to spend time on it.
Most of what a review says would have been cheaper to hear an hour earlier.

This is that hour earlier. It reads a checkout's own diff, picks out what the
team wrote down that bears on it, and says whether the work satisfies it. No
forge, no pull request, no installation: a repository registered on this machine
is the whole of the setup.

What it can answer without a model it always answers: what changed, what
applies, and what has been recorded about the files touched. Judging the work
against prose needs a model, and that part is skipped rather than faked when
nobody has configured one.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, require_local
from src.api.routes.knowledge import get_knowledge
from src.api.routes.local_settings import model_for_this_machine
from src.api.routes.skills import catalogue_for, payload as skill_payload
from src.core.change_context import (
    GitError,
    branch_of,
    commits_since,
    default_branch,
    read_change,
)
from src.core.knowledge import KnowledgeQuery
from src.core.responses import success_response
from src.core.skills import (
    BLOCKING,
    Change,
    ModelSkillChecker,
    PhraseSkillSelector,
    Skill,
    SkillVerdict,
    attach,
    references,
)

router = APIRouter()

MAX_SKILLS = 5
MAX_KNOWLEDGE = 25

# How many baseline documents are worth asking about on their own. More than a
# couple and every review pays for the same answer twice.
MAX_SHARED = 2

HOUSE = "Applies to everything here, whatever the change is about."


def split(chosen) -> list[Skill]:
    """Each skill with what only it points at, and the shared documents on their own.

    A skill is routinely a pointer at the document that holds it, so the
    documents get read with it. But most of a person's skills point at the same
    one file of house preferences, and attaching that to each of them asks the
    same question five times and files every answer under whichever skill was
    asked. A finding about commit messages then arrives under the one about
    page layout.

    A document more than one skill points at is not that skill's content: it is
    what the team expects of everything. It is asked about once, under its own
    name.
    """
    attached = {skill.id: references(skill) for skill in chosen}
    counted = Counter(path for found in attached.values() for path in found)
    shared = [path for path, times in counted.most_common(MAX_SHARED) if times > 1]

    asking = [
        replace(
            skill,
            body=attach(
                skill.body,
                {
                    path: text
                    for path, text in attached[skill.id].items()
                    if path not in shared
                },
            ),
        )
        for skill in chosen
    ]

    for path in shared:
        text = next(found[path] for found in attached.values() if path in found)
        name = Path(path).name
        asking.append(
            Skill(
                id=name,
                name=name,
                description=HOUSE,
                body=text,
                path=path,
                origin="shared",
            )
        )
    return asking


class ReviewInput(BaseModel):
    repository: str = Field(...)
    # What the work is going back to. Empty means whatever the checkout's remote
    # calls its own head, which is right far more often than "main" is.
    against: str = Field(default="")
    title: str = Field(default="")
    description: str = Field(default="")
    # Naming skills pins the review to those; leaving it empty lets the change
    # decide, which is the normal way round.
    skills: list[str] = Field(default_factory=list)
    use_model: bool = Field(default=True)


def verdict_payload(verdict: SkillVerdict) -> dict[str, Any]:
    return {
        "skill": verdict.skill_id,
        "passed": verdict.passed,
        "note": verdict.note,
        "findings": [
            {
                "detail": finding.detail,
                "severity": finding.severity,
                "path": finding.path,
                "line": finding.line,
            }
            for finding in verdict.findings
        ],
    }


@router.post("", dependencies=[Depends(require_local)])
def review(body: ReviewInput, store: Any = Depends(get_knowledge)):
    """Whether this checkout's work is ready to be proposed to anyone."""
    entry = find_repository(body.repository)
    root = Path(entry.path)

    try:
        changes = read_change(
            root,
            entry.scope,
            against=body.against,
            title=body.title,
            description=body.description,
        )
    except GitError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    # Which checkout, on which branch, against what. A person with a worktree
    # open somewhere else is otherwise left wondering whose work this is.
    where = {
        "path": str(root),
        "branch": branch_of(root),
        "against": body.against or default_branch(root),
    }

    if changes is None:
        return success_response(
            {
                "ready": True,
                "changed": [],
                "skills": [],
                "knowledge": [],
                "verdicts": [],
                "where": {**where, "base": "", "commits": 0},
                "note": "Nothing has changed in this checkout.",
            }
        )

    everything = catalogue_for(body.repository).all()
    if body.skills:
        wanted = set(body.skills)
        chosen = tuple(skill for skill in everything if skill.id in wanted)
    else:
        chosen = PhraseSkillSelector().select(
            everything,
            Change(
                title=changes.title,
                description=changes.description,
                paths=changes.paths,
            ),
            limit=MAX_SKILLS,
        )

    recorded = store.search(
        KnowledgeQuery(scope=entry.scope, limit=MAX_KNOWLEDGE)
    ).items

    answer: dict[str, Any] = {
        "changed": [
            {
                "path": item.path,
                "change": item.change,
                # What actually changed in it, so a page can show the work
                # rather than a list of names.
                "patch": dict(item.properties).get("patch", ""),
            }
            for item in changes.files
        ],
        "base": changes.base_revision,
        "where": {
            **where,
            "base": changes.base_revision,
            "commits": commits_since(root, changes.base_revision),
        },
        "skills": [skill_payload(skill) for skill in chosen],
        "knowledge": [
            {"id": item.id, "kind": item.kind, "summary": item.summary}
            for item in recorded
        ],
        "verdicts": [],
        "ready": True,
        "note": "",
    }

    if not body.use_model:
        answer["note"] = "Read what changed and what applies to it. Nothing was judged."
        return success_response(answer)

    provider = model_for_this_machine()
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No model is configured on this machine. Choose one in "
                "Settings, or ask for what changed without judging it."
            ),
        )

    if not chosen:
        answer["note"] = "Nothing on this machine bears on what changed here."
        return success_response(answer)

    subject = Change(
        title=changes.title,
        description=changes.description,
        paths=changes.paths,
        diff=changes.diff,
    )
    checker = ModelSkillChecker(ask=provider.generate_text, model=provider.model)

    whole = split(chosen)
    answer["skills"] = [skill_payload(skill) for skill in whole]

    # One question per rule, asked at the same time. Asked one after another,
    # five rules is a minute of somebody watching a spinner, and a minute is
    # long enough for anything between here and the provider to give up.
    try:
        with ThreadPoolExecutor(max_workers=len(whole)) as pool:
            verdicts = list(
                pool.map(lambda skill: checker.check(skill, subject), whole)
            )
    except Exception as error:  # noqa: BLE001 - whatever a provider raises
        raise HTTPException(status_code=502, detail=str(error)) from error

    answer["verdicts"] = [verdict_payload(verdict) for verdict in verdicts]
    answer["ready"] = not any(
        finding.severity == BLOCKING
        for verdict in verdicts
        for finding in verdict.findings
    )
    return success_response(answer)
