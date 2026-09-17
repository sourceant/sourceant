"""Propose topology relationships from what a model reads in a repository.

A manifest declares what a repository depends on and nothing else. What one
service calls over an interface is written in its code and declared nowhere, so
the manifest reading cannot reach it and something has to look.

The discipline is the manifest reading's. A proposal names a file and a line
span, and that citation is read back before the proposal is kept: a claim whose
lines do not carry what it quoted is dropped rather than believed less. The
entities it may join are fixed before it is asked, every tool answers from
recorded data, and nothing here is applied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional, Sequence

from src.core.topology.models import TopologyEvidence, TopologyRelationship
from src.llms.messages import assistant_message
from src.utils.logger import logger

READ_FILE = "read_file"
SEARCH_CODE = "search_code"
PROPOSE = "propose_connection"

#: How much reading it may do across all rounds.
MAX_READS = 12

#: How many connections one reading may propose.
MAX_PROPOSALS = 10

#: How many times the model may come back for more. Enough to spend the
#: budgets above and still say what it found: fewer ends the conversation
#: while it is still working, and it is told it may read MAX_READS times, so
#: a smaller number here makes that a promise the loop does not keep.
MAX_ROUNDS = MAX_READS + MAX_PROPOSALS + 1

#: What a reading may say, limited to the joints a manifest cannot see. A
#: dependency is declared, so it is the manifest reading's to propose; a call
#: over an interface is written in code and nobody declares it.
KINDS: tuple[str, ...] = ("consumes", "provides", "exposes", "tests")

#: Corroborated by a recorded edge between the same two entities.
CORROBORATED = 0.5

#: The citation reads back and nothing else agrees. Below the 0.6 a manifest
#: gets for a name it cannot prove resolves here, so triage sees it later.
CITED = 0.3

INSTRUCTIONS = (
    "Find which of the systems below this repository actually talks to, by "
    "reading its code.\n\n"
    "A dependency it declares is already known and is not what you are for. "
    "What you are for is the joint nobody declared: the URL it calls, the "
    "queue it publishes to, the endpoint it serves that another system reads. "
    "Those are written in code, usually in a client, a config value or a route "
    "definition.\n\n"
    "Propose a connection only where you have read the line that makes it. "
    "Every proposal names the file and the lines, and quotes them. The quote "
    "is read back against the file: one that is not there takes the whole "
    "proposal with it, so quote what you actually saw rather than what you "
    "expect the line to say.\n\n"
    "A system you cannot connect is the ordinary answer. Propose nothing "
    f"rather than something you did not read. You may read up to {MAX_READS} "
    "times; answer with no tool call once you are done."
)


@dataclass(frozen=True)
class Reading:
    """One connection a model says it read, before anything is checked."""

    target_id: str
    kind: str
    path: str
    start_line: int
    end_line: int
    quote: str
    because: str = ""


@dataclass(frozen=True)
class Checked:
    """A reading, and whether its citation reads back."""

    reading: Reading
    kept: bool
    reason: str = ""
    corroborated: bool = False


def tools_for(targets: Sequence[str]) -> list:
    """The three things a reading may do, and the entities it may name."""
    return [
        {
            "type": "function",
            "function": {
                "name": READ_FILE,
                "description": "Read one file of this repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path from the root of the repository.",
                        }
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": SEARCH_CODE,
                "description": (
                    "Find where something appears in this repository: a URL, a "
                    "queue name, an endpoint, any text."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "terms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Between one and sixteen things to look for.",
                        }
                    },
                    "required": ["terms"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": PROPOSE,
                "description": (
                    "Say that this repository is joined to one of these "
                    "systems, citing the lines that say so."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "enum": list(targets),
                            "description": "Which system this repository is joined to.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": list(KINDS),
                            "description": "What kind of joint it is.",
                        },
                        "path": {
                            "type": "string",
                            "description": "The file the joint is written in.",
                        },
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                        "quote": {
                            "type": "string",
                            "description": (
                                "The text of those lines, copied. It is read "
                                "back against the file."
                            ),
                        },
                        "because": {
                            "type": "string",
                            "description": "What those lines say, in one sentence.",
                        },
                    },
                    "required": [
                        "target",
                        "kind",
                        "path",
                        "start_line",
                        "end_line",
                        "quote",
                    ],
                },
            },
        },
    ]


def _tightened(text: str) -> str:
    """Text with whitespace taken out, for comparing a quote to a file.

    A model reproduces the characters of a line and not its indentation, and a
    quote rejected over a tab would drop a proposal that is correct.
    """
    return "".join(text.split())


def check(reading: Reading, contents: Optional[str]) -> Checked:
    """Whether the cited lines carry what the reading quoted."""
    if contents is None:
        return Checked(reading, False, f"{reading.path} could not be read")
    lines = contents.splitlines()
    if not 1 <= reading.start_line <= reading.end_line <= len(lines):
        return Checked(
            reading,
            False,
            f"{reading.path} has no lines {reading.start_line}-{reading.end_line}",
        )
    cited = "\n".join(lines[reading.start_line - 1 : reading.end_line])
    quoted = _tightened(reading.quote)
    if not quoted or quoted not in _tightened(cited):
        return Checked(
            reading,
            False,
            f"{reading.path} lines {reading.start_line}-{reading.end_line} "
            "do not say what was quoted",
        )
    return Checked(reading, True)


def relationship_of(
    entity_id: str, checked: Checked, repository: str, revision: str = ""
) -> TopologyRelationship:
    """A kept reading as a proposal, carrying the lines it was read from."""
    one = checked.reading
    where = f"{repository}/{one.path}#L{one.start_line}-L{one.end_line}"
    return TopologyRelationship(
        id=f"{entity_id}->{one.target_id}:{one.kind}",
        source_id=entity_id,
        target_id=one.target_id,
        type=one.kind,
        # Proposed, never applied. A person decides whether it is real.
        status="pending",
        confidence=CORROBORATED if checked.corroborated else CITED,
        properties={
            "inferred_from": "reading",
            "because": one.because,
            "corroborated": checked.corroborated,
        },
        evidence=(
            TopologyEvidence(
                id=where,
                kind="reading",
                source=where,
                revision=revision,
                properties={
                    "path": one.path,
                    "start_line": one.start_line,
                    "end_line": one.end_line,
                    "quote": one.quote,
                },
            ),
        ),
    )


class WhatItReads:
    """Asks a model what this repository talks to, and checks what it says."""

    def __init__(
        self,
        read_file: Callable[[str], Optional[str]],
        search: Callable[[Sequence[str]], Iterable[Mapping[str, object]]],
        corroborates: Callable[[str, str], bool] = lambda source, target: False,
        *,
        rounds: int = MAX_ROUNDS,
    ) -> None:
        self._read_file = read_file
        self._search = search
        #: Whether the code index already records an edge between the two, which
        #: is the difference between a reading nothing else agrees with and one
        #: that lines up with what was indexed.
        self._corroborates = corroborates
        self._rounds = rounds

    #: Set when the model could not be asked at all. A reading that found
    #: nothing and one that never ran look identical in what comes back, and
    #: only the first is an answer.
    refused: str = ""

    #: Set when the rounds ran out while the model was still asking for tools.
    #: What it had proposed by then is kept, but the repository was not read to
    #: the end and must not be recorded as though it had been.
    unfinished: bool = False

    def propose(
        self,
        provider,
        *,
        entity_id: str,
        repository: str,
        targets: Sequence[str],
        about: str = "",
        revision: str = "",
    ) -> tuple[TopologyRelationship, ...]:
        """Every connection the reading proposed whose citation reads back."""
        self.refused = ""
        self.unfinished = False
        if not targets or not entity_id:
            return ()
        if not hasattr(provider, "ask_with_tools"):
            self.refused = "this model cannot be asked to read"
            return ()

        tools = tools_for(targets)
        messages = [
            {
                "role": "user",
                "content": (
                    f"{INSTRUCTIONS}\n\nThe repository is {repository}.\n\n"
                    f"The systems it might be joined to:\n{about}"
                ),
            }
        ]
        readings: list[Reading] = []
        reads = 0
        done = False
        for round_number in range(self._rounds):
            try:
                answer = provider.ask_with_tools(
                    messages,
                    tools,
                    purpose="topology_reading",
                    require=round_number == 0,
                )
            except Exception as error:  # noqa: BLE001 - reading, not reviewing
                logger.warning("A repository could not be read for joints: %s", error)
                self.refused = f"the model could not be asked: {type(error).__name__}"
                break
            calls = answer.get("tool_calls") or ()
            if not calls:
                done = True
                break
            messages.append(assistant_message(answer))
            for call in calls:
                spoken, proposed = self._run(call, targets, reads)
                if proposed is not None:
                    readings.append(proposed)
                else:
                    reads += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": spoken,
                    }
                )
            if reads >= MAX_READS or len(readings) >= MAX_PROPOSALS:
                done = True
                break

        self.unfinished = not done and not self.refused
        return self._kept(readings, entity_id, repository, revision)

    def _kept(
        self,
        readings: Sequence[Reading],
        entity_id: str,
        repository: str,
        revision: str,
    ) -> tuple[TopologyRelationship, ...]:
        contents: dict[str, Optional[str]] = {}
        proposals: dict[str, TopologyRelationship] = {}
        for reading in readings[:MAX_PROPOSALS]:
            if reading.path not in contents:
                contents[reading.path] = self._read(reading.path)
            checked = check(reading, contents[reading.path])
            if not checked.kept:
                logger.info("Dropped a proposed connection: %s", checked.reason)
                continue
            corroborated = self._agrees(entity_id, reading.target_id)
            one = relationship_of(
                entity_id,
                Checked(reading, True, corroborated=corroborated),
                repository,
                revision,
            )
            # The same joint read twice is one proposal carrying both citations.
            standing = proposals.get(one.id)
            proposals[one.id] = (
                one
                if standing is None
                else TopologyRelationship(
                    id=standing.id,
                    source_id=standing.source_id,
                    target_id=standing.target_id,
                    type=standing.type,
                    status=standing.status,
                    confidence=max(standing.confidence, one.confidence),
                    properties=standing.properties,
                    evidence=standing.evidence + one.evidence,
                )
            )
        return tuple(proposals.values())

    def _agrees(self, entity_id: str, target_id: str) -> bool:
        try:
            return bool(self._corroborates(entity_id, target_id))
        except Exception as error:  # noqa: BLE001 - one check, not the reading
            logger.warning("Could not check the index for %s: %s", target_id, error)
            return False

    def _read(self, path: str) -> Optional[str]:
        try:
            return self._read_file(path)
        except Exception as error:  # noqa: BLE001 - one file, not the reading
            logger.warning("Could not read %s: %s", path, error)
            return None

    def _run(self, call, targets, reads) -> tuple[str, Optional[Reading]]:
        name = call.get("name") or ""
        try:
            asked = json.loads(call.get("arguments") or "{}")
        except ValueError:
            return json.dumps({"nothing": "that request could not be read"}), None

        if name == PROPOSE:
            return self._proposed(asked, targets)
        if reads >= MAX_READS:
            return json.dumps({"nothing": "no reading left"}), None
        if name == READ_FILE:
            path = str(asked.get("path") or "")
            contents = self._read(path) if path else None
            if contents is None:
                return json.dumps({"read": path, "nothing": "not readable"}), None
            return json.dumps({"read": path, "lines": _numbered(contents)}), None
        if name == SEARCH_CODE:
            terms = tuple(str(term) for term in (asked.get("terms") or ()))[:16]
            if not terms:
                return json.dumps({"nothing": "no terms given"}), None
            try:
                matches = list(self._search(terms))
            except Exception as error:  # noqa: BLE001 - one search, not the reading
                logger.warning("Searching for joints failed: %s", error)
                return json.dumps({"nothing": f"search failed: {error}"}), None
            return (
                json.dumps({"terms": list(terms), "matches": matches}, sort_keys=True),
                None,
            )
        return json.dumps({"nothing": f"{name} is not something you can do"}), None

    @staticmethod
    def _proposed(asked, targets) -> tuple[str, Optional[Reading]]:
        target = str(asked.get("target") or "")
        kind = str(asked.get("kind") or "")
        # Both lists were fixed before the model was asked.
        if target not in targets:
            return json.dumps({"nothing": "that is not one of the systems"}), None
        if kind not in KINDS:
            return json.dumps({"nothing": f"{kind} is not a kind of joint"}), None
        try:
            start = int(asked.get("start_line") or 0)
            end = int(asked.get("end_line") or 0)
        except (TypeError, ValueError):
            return json.dumps({"nothing": "those are not line numbers"}), None
        reading = Reading(
            target_id=target,
            kind=kind,
            path=str(asked.get("path") or ""),
            start_line=start,
            end_line=end,
            quote=str(asked.get("quote") or ""),
            because=str(asked.get("because") or ""),
        )
        return json.dumps({"noted": target, "kind": kind}), reading


def _numbered(contents: str, most: int = 400) -> list[str]:
    """The file with its line numbers, so a citation can name them."""
    return [
        f"{number}: {line}"
        for number, line in enumerate(contents.splitlines()[:most], start=1)
    ]
