"""Asking a model for what a repository has not written down.

Reading a repository's own words is the floor: it is exact, needs nothing, and
finds only what somebody already bothered to type. Most of what a team knows was
never typed anywhere, and getting at that means asking something that can read
code and infer.

What comes back is proposals and is marked as such. Nothing here decides that
something is true; it decides that something is worth a person looking at. The
same policy that rejects an inventory summary from any other source rejects one
from here, because a model asked what a repository knows will happily answer
that it uses PostgreSQL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from src.core.initialization.models import InitializationCandidate
from src.core.initialization.policy import DefaultInitializationCandidatePolicy

from .models import KnowledgeObject

KINDS = ("decision", "convention", "constraint", "pattern", "workaround")

# Enough for a model to see the shape of a repository without paying to send it
# the repository.
MAX_EVIDENCE = 12_000
MAX_PROPOSALS = 12

PROMPT = """You are reading a codebase to find what its team knows but never wrote down.

Repository: {repository}

What it contains:
{layout}

What it says about itself:
{prose}

Already recorded, so do not repeat any of it:
{known}

Answer with a JSON array, at most {limit} entries, each an object with:
  "id":      a short kebab-case name, unique
  "kind":    one of {kinds}
  "summary": one sentence stating what is true, in the present tense
  "why":     one sentence on why it is that way, or "" if the code does not say

Only include something a person maintaining this would need to know and could
not tell at a glance. Do not describe what the repository contains, which
languages or libraries it uses, or how to install it: that is inventory, not
knowledge. If nothing qualifies, answer with an empty array.

Answer with the JSON array and nothing else."""

FENCED = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


@dataclass(frozen=True)
class Proposal:
    knowledge: KnowledgeObject
    # What was asked, so a person can tell a proposal from a reading.
    model: str


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…"


def _parse(answer: str) -> list[dict]:
    """The array a model was asked for, out of whatever it actually sent.

    Models fence JSON, preface it, or apologise around it. Anything that is not
    a list of objects is nothing, rather than something half-read.
    """
    text = (answer or "").strip()
    fenced = FENCED.search(text)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    return (
        [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, list)
        else []
    )


def propose(
    repository: str,
    layout: Sequence[str],
    prose: str,
    known: Iterable[KnowledgeObject],
    ask: Callable[[str], str],
    model: str = "",
    limit: int = MAX_PROPOSALS,
) -> list[Proposal]:
    """What a model thinks is worth recording, filtered by the same rules as everything else."""
    recorded = [item.summary for item in known]
    prompt = PROMPT.format(
        repository=repository,
        layout=_shorten("\n".join(layout), MAX_EVIDENCE // 3),
        prose=_shorten(prose, MAX_EVIDENCE) or "Nothing.",
        known="\n".join(f"- {summary}" for summary in recorded) or "Nothing yet.",
        limit=limit,
        kinds=", ".join(KINDS),
    )

    policy = DefaultInitializationCandidatePolicy()
    proposals: list[Proposal] = []
    seen: set[str] = set()

    for item in _parse(ask(prompt))[:limit]:
        identifier = str(item.get("id") or "").strip()
        summary = str(item.get("summary") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if not identifier or not summary or kind not in KINDS or identifier in seen:
            continue
        why = str(item.get("why") or "").strip()
        # A model asked what a repository knows will answer that it uses
        # PostgreSQL. The same rule that rejects that from anywhere else
        # rejects it here.
        candidate = InitializationCandidate(
            kind=kind,
            slug=identifier,
            summary=summary,
            rationale=why,
            future_decision="",
            invalidation="",
            evidence_ids=(),
        )
        if not policy.assess(candidate).accepted:
            continue
        seen.add(identifier)

        properties = {"source": "model", "model": model}
        if why:
            properties["why"] = why
        proposals.append(
            Proposal(
                KnowledgeObject(
                    id=identifier,
                    kind=kind,
                    status="proposed",
                    summary=summary,
                    properties=properties,
                ),
                model=model,
            )
        )
    return proposals
