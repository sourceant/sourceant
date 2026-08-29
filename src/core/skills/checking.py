"""Putting a change through a skill.

A skill is prose: it says how work here is meant to be done. Judging whether a
change follows prose is not something a rule engine does, so it is asked of a
model, one skill at a time. One at a time because a model asked to hold six
documents at once answers about the loudest of them.

What comes back is a verdict per skill, and a verdict blocks only when the skill
itself was written as a rule. Anything a model is unsure of is advisory, because
a check that stops work on a maybe is a check people turn off.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .models import (
    ADVISORY,
    BLOCKING,
    Change,
    Skill,
    SkillFinding,
    SkillVerdict,
)

MAX_DIFF = 40_000
MAX_FINDINGS = 10

PROMPT = """You are checking one change against one rule its team wrote.

The rule, titled "{name}":
{skill}

The change{titled}:
{description}

Files it touches:
{paths}

What it does:
{diff}

Answer with a JSON object:
  "passed":   true if the change follows the rule, false if it does not
  "note":     one sentence saying why, in the present tense
  "findings": an array, at most {limit} entries, each an object with
              "detail"   what is wrong, and what to do instead
              "severity" "blocking" when the rule states a requirement the
                         change breaks, "advisory" otherwise
              "path"     the file it is about, or ""
              "line"     the line number in that file, or null

Judge only against this rule. Say nothing about anything the rule does not
cover, however wrong it looks. If the rule does not apply to this change,
answer passed with an empty findings array.

Answer with the JSON object and nothing else."""

FENCED = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…"


def _parse(answer: str) -> dict:
    """The object a model was asked for, out of whatever it actually sent."""
    text = (answer or "").strip()
    fenced = FENCED.search(text)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class ModelSkillChecker:
    """One skill, one change, one question.

    An unreadable answer is not a failure: a model that returned nothing usable
    has said nothing about the change, and reporting that as a breach would put
    a wrong stop in front of somebody's work.
    """

    ask: Callable[[str], str]
    model: str = ""

    def check(self, skill: Skill, change: Change) -> SkillVerdict:
        titled = f' titled "{change.title}"' if change.title else ""
        prompt = PROMPT.format(
            name=skill.name,
            skill=_shorten(f"{skill.description}\n\n{skill.body}".strip(), 12_000),
            titled=titled,
            description=change.description or "Nothing was written about it.",
            paths="\n".join(f"- {path}" for path in change.paths) or "None listed.",
            diff=_shorten(change.diff, MAX_DIFF) or "Nothing.",
            limit=MAX_FINDINGS,
        )

        answered = _parse(self.ask(prompt))
        if not answered:
            return SkillVerdict(
                skill_id=skill.id,
                passed=True,
                note="Nothing usable came back, so this rule was not applied.",
            )

        findings: list[SkillFinding] = []
        for item in answered.get("findings", [])[:MAX_FINDINGS]:
            if not isinstance(item, dict):
                continue
            detail = str(item.get("detail") or "").strip()
            if not detail:
                continue
            severity = str(item.get("severity") or "").strip().lower()
            line = item.get("line")
            findings.append(
                SkillFinding(
                    detail=detail,
                    severity=BLOCKING if severity == BLOCKING else ADVISORY,
                    path=str(item.get("path") or ""),
                    line=line if isinstance(line, int) else None,
                )
            )

        return SkillVerdict(
            skill_id=skill.id,
            passed=bool(answered.get("passed", True)),
            findings=tuple(findings),
            note=str(answered.get("note") or "").strip(),
        )
