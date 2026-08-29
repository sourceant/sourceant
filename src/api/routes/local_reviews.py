"""Reviewing work before anybody else sees it.

The hosted review happens after a pull request exists, which is after the author
has decided the work is finished and asked other people to spend time on it.
Most of what a review says would have been cheaper to hear an hour earlier.

This is that hour earlier. It reads a checkout's own diff, picks out what the
team wrote down that bears on it, and says whether the work satisfies it. No
forge, no pull request, no installation: a repository registered on this machine
is the whole of the setup.

It is the same review the hosted path gives a pull request, from the same
generator: an overview, what is worth changing, line by line suggestions with
the code to put there, and a verdict. What differs is only where the change
comes from and what is known about it.

What a team has written down goes in as context rather than as the review
itself. Their recorded decisions and the skills they taught their coding agents
are what a reviewer here would know and a stranger would not, so they are given
to the same reviewer rather than checked by a second one standing beside it.

What it can answer without a model it always answers: what changed and what
applies to it. Reviewing needs a model, and that part is skipped rather than
faked when nobody has configured one.

A review is asked for by one thing and read by another. An agent runs one over
MCP while somebody is in the middle of something else and hands them a link, so
asking for one answers with a name straight away and the reading happens behind
it. The answer is kept, because the link has to still open it an hour later.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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
from src.config.db import get_engine
from src.core.knowledge import KnowledgeQuery
from src.core.local_reviews import (
    DONE,
    FAILED,
    RUNNING,
    LocalReview,
    SQLLocalReviewStore,
    named,
)
from datetime import timedelta

from src.core.local_reviews.models import now
from src.core.responses import success_response
from src.utils.diff_parser import parse_diff
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

_kept: Any = None


def get_reviews() -> Any:
    """Where reviews are kept on this machine.

    The schema is made if it is missing rather than waited for: this table is
    local, and a person who started the agent should not have to know that
    migrations are a thing.
    """
    global _kept
    if _kept is None:
        engine = get_engine()
        if engine is None:
            raise HTTPException(
                status_code=503, detail="This machine has nowhere to keep a review"
            )
        _kept = SQLLocalReviewStore(engine, create_schema=True)
    return _kept


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


def told(recorded, skills) -> str:
    """What a team has written down, for the reviewer to read before it starts.

    A reviewer who has not read these is a stranger with an opinion. The point
    of any of this is that the review knows what the team already decided.
    """
    parts: list[str] = []

    if recorded:
        parts.append("## What this team has already decided\n")
        parts.append(
            "Recorded against this repository. A change that contradicts one of "
            "these is worth raising even where the code reads well.\n"
        )
        for item in recorded:
            why = dict(item.properties).get("why", "")
            parts.append(f"- **{item.id}** ({item.kind}): {item.summary}")
            if why:
                parts.append(f"  Why: {why}")
        parts.append("")

    if skills:
        parts.append("## What this team expects of work here\n")
        parts.append(
            "Written by the team for whatever reads their code. Judge the change "
            "against these as well as against the code itself, and say which one "
            "a suggestion comes from where it comes from one.\n"
        )
        for skill in skills:
            parts.append(f"### {skill.name}")
            if skill.description:
                parts.append(f"_{skill.description}_")
            body = (skill.body or "").strip()
            if body:
                parts.append(body[:6_000])
            parts.append("")

    return "\n".join(parts)


def reviewed(review) -> dict[str, Any]:
    """One review, in the shape a screen draws and an agent reads."""
    if review is None:
        return {}
    summary = review.summary
    return {
        "verdict": review.verdict.value if review.verdict else "",
        "summary": {
            "overview": summary.overview if summary else "",
            "key_improvements": list(summary.key_improvements) if summary else [],
            "minor_suggestions": list(summary.minor_suggestions) if summary else [],
            "critical_issues": list(summary.critical_issues) if summary else [],
        },
        "suggestions": [
            {
                "path": one.file_name,
                "start_line": one.start_line,
                "end_line": one.end_line,
                "side": one.side.value if one.side else "",
                "comment": one.comment,
                "category": one.category.value if one.category else "",
                "suggested_code": one.suggested_code or "",
            }
            for one in (review.code_suggestions or [])
        ],
        # The prose sections the reviewer fills in where it has something to say.
        "notes": {
            name: getattr(review, name)
            for name in (
                "code_quality",
                "potential_bugs",
                "performance",
                "readability",
                "security",
                "refactoring_suggestions",
                "documentation_suggestions",
            )
            if getattr(review, name)
        },
    }


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


def read_and_judge(body: ReviewInput, store: Any) -> dict[str, Any]:
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
        return {
            "ready": True,
            "changed": [],
            "skills": [],
            "knowledge": [],
            "verdicts": [],
            "review": {},
            "where": {**where, "base": "", "commits": 0},
            "note": "Nothing has changed in this checkout.",
        }

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
        "review": {},
        "ready": True,
        "note": "",
    }

    if not body.use_model:
        answer["note"] = "Read what changed and what applies to it. Nothing was judged."
        return answer

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
        return answer

    # The review proper, from the same generator the hosted path uses, told
    # what this team has written down.
    try:
        review = provider.generate_code_review(
            diff=changes.diff,
            parsed_files=parse_diff(changes.diff),
            pr_metadata={
                "title": changes.title or f"Work on {where['branch']}",
                "description": changes.description,
                "author": "",
            },
            knowledge=told(recorded, chosen),
        )
    except Exception as error:  # noqa: BLE001 - whatever a provider raises
        raise HTTPException(status_code=502, detail=str(error)) from error

    answer["review"] = reviewed(review)

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
    blocked = any(
        finding.severity == BLOCKING
        for verdict in verdicts
        for finding in verdict.findings
    )
    answer["ready"] = (
        not blocked and answer["review"].get("verdict") != "REQUEST_CHANGES"
    )
    return answer


# Longer than any review takes, including one asking a slow provider about
# every skill on a machine. Past this, whatever was doing the reading is gone.
ABANDONED = timedelta(minutes=30)


def kept(review: LocalReview) -> dict[str, Any]:
    """One review, in the shape a screen and an agent both read.

    A review still running long after anything could still be reading it was
    abandoned: the process doing it went away. Saying so beats a spinner that
    never stops.
    """
    status, error = review.status, review.error
    if status == RUNNING and now() - review.started > ABANDONED:
        status = FAILED
        error = (
            "Whatever was reading this stopped before it finished. Nothing was "
            "changed; ask for it again."
        )
    return {
        "id": review.id,
        "repository": review.repository,
        "status": status,
        "title": review.title,
        "error": error,
        "started": review.started.isoformat() if review.started else None,
        "finished": review.finished.isoformat() if review.finished else None,
        "review": dict(review.answer),
        # Where to send somebody who was handed this by an agent.
        "path": f"#/reviews/{review.id}",
    }


def run(identifier: str, body: ReviewInput, store: Any, reviews: Any) -> None:
    """Do the reading, and keep whatever came of it.

    Nothing raised here reaches anybody: the request that asked for it has long
    since been answered, so a failure is written down rather than thrown.
    """
    try:
        answer = read_and_judge(body, store)
    except HTTPException as refused:
        reviews.put(
            LocalReview(
                id=identifier,
                repository=body.repository,
                status=FAILED,
                error=str(refused.detail),
                title=body.title,
                finished=now(),
            )
        )
        return
    except Exception as error:  # noqa: BLE001 - whatever went wrong is the answer
        reviews.put(
            LocalReview(
                id=identifier,
                repository=body.repository,
                status=FAILED,
                error=str(error),
                title=body.title,
                finished=now(),
            )
        )
        return

    reviews.put(
        LocalReview(
            id=identifier,
            repository=body.repository,
            status=DONE,
            answer=answer,
            title=body.title,
            finished=now(),
        )
    )


@router.post("", dependencies=[Depends(require_local)])
def start_review(
    body: ReviewInput,
    background: BackgroundTasks,
    store: Any = Depends(get_knowledge),
    reviews: Any = Depends(get_reviews),
):
    """Ask for a review, and get back where to find it.

    The name comes back before the reading starts, so whatever asked can hand
    somebody a link and get on with something else.
    """
    # Checked here rather than in the background, so naming a repository nobody
    # covers is refused rather than written down as a failed review.
    find_repository(body.repository)

    identifier = named()
    started = reviews.put(
        LocalReview(id=identifier, repository=body.repository, title=body.title)
    )
    background.add_task(run, identifier, body, store, reviews)
    return success_response(kept(started))


@router.get("", dependencies=[Depends(require_local)])
def read_reviews(
    repository: str = "",
    limit: int = 20,
    reviews: Any = Depends(get_reviews),
):
    """The last few, newest first."""
    found = reviews.recent(repository=repository, limit=max(1, min(limit, 100)))
    return success_response([{**kept(review), "review": {}} for review in found])


@router.get("/{identifier}", dependencies=[Depends(require_local)])
def read_review(identifier: str, reviews: Any = Depends(get_reviews)):
    """One review, whether it is still running or long finished."""
    found = reviews.get(identifier)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail="No review by that name. It may have been one of the oldest.",
        )
    return success_response(kept(found))
