"""Reviews a checkout: the second way in to the reviewer in ``reviewing.py``.

``plugin.py`` is the first, taking a diff from a forge and posting comments
back. This one takes a folder and returns the answer for a screen to draw.

Folders and skills are resolved from whatever registered them.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

from src.core.change_context import (
    GitError,
    branch_of,
    commits_in,
    commits_since,
    default_branch,
    read_change,
)
from src.core.knowledge import KnowledgeQuery
from src.core.model import provider_for
from src.core.review import Told, reviewer
from src.utils.logger import logger
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

from src.core.environment import LOCAL
from src.core.repositories import registry
from src.core.services import ServiceRegistry, service_registry
from src.core.skills import SkillLibrary

MAX_SKILLS = 5
MAX_KNOWLEDGE = 25

# How many baseline documents are worth asking about on their own. More than a
# couple and every review pays for the same answer twice.
MAX_SHARED = 2

HOUSE = "Applies to everything here, whatever the change is about."


class ReviewRefused(Exception):
    """A review that cannot be run, with the status to answer with.

    Carries the status rather than raising an HTTP error, since a plugin does
    not know it is behind a web server.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def split(chosen) -> list[Skill]:
    """Each skill with the documents only it points at, plus shared ones alone.

    A document more than one skill references is not that skill's content, so
    attaching it to each would ask the same question repeatedly and file every
    answer under an unrelated skill.
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


def on_disk(root: Path):
    """Reads a changed file from the checkout."""
    seen: dict[str, str | None] = {}

    def read(path: str) -> str | None:
        if path not in seen:
            target = root / path
            try:
                seen[path] = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                seen[path] = None
        return seen[path]

    return read


def told(recorded, skills) -> tuple[Told, ...]:
    """Recorded decisions and skills, as prose under headings.

    The reviewer has no vocabulary for a skill, so each arrives as text.
    """
    sections: list[Told] = []

    if recorded:
        lines = [
            "Recorded against this repository. A change that contradicts one of "
            "these is worth raising even where the code reads well.\n"
        ]
        for item in recorded:
            why = dict(item.properties).get("why", "")
            lines.append(f"- **{item.id}** ({item.kind}): {item.summary}")
            if why:
                lines.append(f"  Why: {why}")
        sections.append(Told("What this team has already decided", "\n".join(lines)))

    if skills:
        lines = [
            "Written by the team for whatever reads their code. Judge the change "
            "against these as well as against the code itself, and say which one "
            "a suggestion comes from where it comes from one.\n"
        ]
        for skill in skills:
            lines.append(f"### {skill.name}")
            if skill.description:
                lines.append(f"_{skill.description}_")
            body = (skill.body or "").strip()
            if body:
                lines.append(body[:6_000])
            lines.append("")
        sections.append(Told("What this team expects of work here", "\n".join(lines)))

    return tuple(sections)


def remember(review, scope, services, repository: str = "") -> None:
    """Keep what a review said, so the next one knows it has said it before.

    Off unless asked for: a reviewer that rewords itself raises the odd
    duplicate under any fingerprint. Nothing here fails a review.
    """
    from src.core.review import OPEN, ReviewFinding, finding_store, prints_for
    from src.core.settings.resolver import value_of

    if not value_of("review.remember_findings", repository=repository):
        return

    kept = finding_store(services)
    if kept is None or review is None:
        return

    for one in review.code_suggestions or ():
        names = prints_for(one.file_name, one.comment or "", one.suggested_code or "")
        try:
            # Under any name it already answers to, so a finding whose wording
            # changed but whose code did not keeps the state somebody set.
            known = next(
                (
                    name
                    for name in names
                    if getattr(kept, "get_finding", None)
                    and kept.get_finding(scope, name) is not None
                ),
                names[0],
            )
            kept.put_finding(
                scope,
                ReviewFinding(
                    id=known,
                    state=OPEN,
                    summary=one.comment or "",
                    # Where it was last seen. Useful to a reader, and never the
                    # identity: a line moves, a finding does not.
                    code_anchor=f"{one.file_name}:{one.start_line}",
                    properties={
                        "category": one.category.value if one.category else "",
                        "also": list(names[1:]),
                    },
                ),
            )
        except Exception as error:  # noqa: BLE001 - whatever a store raises
            # This one, not the rest. A store that refuses one finding is no
            # reason to forget the others a review just made.
            logger.warning(f"A finding was not kept: {error}")
            continue


def reviewed(review) -> dict[str, Any]:
    """One review, in the shape a screen and an agent read."""
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
                # Both sides, so a suggestion reads as a change rather than as
                # an addition out of nowhere.
                "existing_code": one.existing_code or "",
                "suggested_code": one.suggested_code or "",
            }
            for one in (review.code_suggestions or [])
        ],
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


def skill_payload(skill: Skill, full: bool = False) -> dict[str, Any]:
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
    return listed


@dataclass
class WorkingTreeReviews:
    """Assembles a checkout's change and asks the registered reviewer about it."""

    repositories: Any = None
    skills: SkillLibrary | None = None
    knowledge: Any = None
    services: ServiceRegistry = field(default=service_registry)

    def _folders(self):
        found = self.repositories or registry(self.services)
        if found is None:
            raise ReviewRefused(503, "Nothing here knows which folders to review.")
        return found

    def _library(self) -> SkillLibrary:
        found = self.skills
        if found is None:
            try:
                found = self.services.resolve(SkillLibrary)
            except LookupError:
                found = None
        if found is None:
            raise ReviewRefused(503, "Nothing here knows where the skills are.")
        return found

    def review(
        self,
        *,
        repository: str,
        against: str = "",
        title: str = "",
        description: str = "",
        skills: Sequence[str] = (),
        use_model: bool = True,
    ) -> dict[str, Any]:
        """What changed, what applies to it, and what the reviewer made of it."""
        entry = self._folders().named(LOCAL, repository)
        root = Path(entry.path)

        try:
            changes = read_change(
                root,
                entry.scope,
                against=against,
                title=title,
                description=description,
            )
        except GitError as error:
            raise ReviewRefused(400, str(error)) from error

        # Named explicitly: several worktrees of one repository look alike.
        where = {
            "path": str(root),
            "branch": branch_of(root),
            "against": against or default_branch(root),
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

        everything = self._library().all(LOCAL, repository)
        if skills:
            wanted = set(skills)
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

        recorded = ()
        if self.knowledge is not None:
            recorded = self.knowledge.search(
                KnowledgeQuery(scope=entry.scope, limit=MAX_KNOWLEDGE)
            ).items

        answer: dict[str, Any] = {
            "changed": [
                {
                    "path": item.path,
                    "change": item.change,
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
            "commits": commits_in(root, changes.base_revision),
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

        if not use_model:
            answer["note"] = (
                "Read what changed and what applies to it. Nothing was judged."
            )
            return answer

        provider = provider_for(user=LOCAL)
        if provider is None:
            raise ReviewRefused(
                400,
                "No model is configured. Choose one in "
                "Settings, or ask for what changed without judging it.",
            )

        if not chosen:
            answer["note"] = "No skill here bears on what changed."
            return answer

        judge = reviewer(self.services)
        if judge is None:
            raise ReviewRefused(
                503, "Nothing here is able to review. The code reviewer is not loaded."
            )

        try:
            review = judge.review(
                replace(changes, title=changes.title or f"Work on {where['branch']}"),
                provider=provider,
                read_content=on_disk(root),
                told=told(recorded, chosen),
                # A checkout is indexed as it is, not as a commit, so the
                # graph is filed under the repository alone.
                code_scope=entry.scope,
            )
        except ReviewRefused:
            raise
        except Exception as error:  # noqa: BLE001 - whatever a provider raises
            raise ReviewRefused(502, str(error)) from error

        if review is None:
            answer["note"] = "Nothing in this change could be read as code."
            return answer

        answer["review"] = reviewed(review)
        remember(review, entry.scope, self.services, repository)

        subject = Change(
            title=changes.title,
            description=changes.description,
            paths=changes.paths,
            diff=changes.diff,
        )
        checker = ModelSkillChecker(ask=provider.generate_text, model=provider.model)

        whole = split(chosen)
        answer["skills"] = [skill_payload(skill) for skill in whole]

        # Concurrent: asked in turn, five rules is a minute, which is long
        # enough for something in between to time out.
        try:
            with ThreadPoolExecutor(max_workers=len(whole)) as pool:
                verdicts = list(
                    pool.map(lambda skill: checker.check(skill, subject), whole)
                )
        except Exception as error:  # noqa: BLE001 - whatever a provider raises
            raise ReviewRefused(502, str(error)) from error

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
