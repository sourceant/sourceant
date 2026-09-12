"""Letting the review say what it wants to look at.

What is worth searching for is often not in the diff. Change a route and the
thing that breaks is the client that calls it, under a name this change never
mentions. No rule over the text of a diff reaches that, and the rule that was
there pulled words out of comments and searched other repositories for them.

So the model is asked, and it can come back for more. It chooses the words; it
does not choose what they mean. The search itself stays deterministic, the
repositories it may ask about are fixed before it is asked, and everything it
gets back is data.
"""

from __future__ import annotations

import json

from src.core.search import SearchQuery
from src.utils.logger import logger

from .models import Asked, MAX_ROUNDS, MAX_SEARCHES

SEARCH_CODE = "search_code"

INSTRUCTIONS = (
    "You are about to review the change below. First find out who else this "
    "change affects, by searching the repositories it reaches.\n\n"
    "One question decides what to search for: which code outside this "
    "repository would stop working if this change shipped? Look for the "
    "callers of anything renamed, removed, or given a different signature; "
    "the consumers of a response field or an endpoint that changed; whoever "
    "implements an interface this change alters. Search under the OLD name, "
    "because the code about to break still uses it.\n\n"
    "Search each repository for what would break in THAT repository. They are "
    "joined to this one in different ways, and the same words will not do for "
    "all of them: a repository that imports this code breaks on a renamed "
    "symbol, one that calls it over an interface breaks on a route or a field "
    "name and contains no symbol from here at all.\n\n"
    "Every repository listed is worth one search unless you can say why it is "
    "not. A repository you do not search is reported as unread, so silence "
    "there is not neutral.\n\n"
    "A term is any text a search can match: a name, a path, a snake_case "
    "identifier, an endpoint, a fragment of a line. Ask for what you need and "
    f"stop; you may search up to {MAX_SEARCHES} times in total. Answer with "
    "no tool call once you have enough."
)


def tools_for(repositories: tuple[str, ...]) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": SEARCH_CODE,
                "description": (
                    "Find who uses a name in one of the repositories this "
                    "change reaches: its callers, its consumers, whatever "
                    "would break if it changed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository": {
                            "type": "string",
                            "enum": list(repositories),
                            "description": "Which repository to search.",
                        },
                        "terms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Between one and sixteen things to look for. "
                                "A name, a path, an endpoint, any text."
                            ),
                        },
                    },
                    "required": ["repository", "terms"],
                },
            },
        }
    ]


#: What each kind of joint means for what would break across it. A repository
#: that imports this code breaks on a renamed symbol; one that calls it over
#: HTTP breaks on a route or a response field and shares no symbol at all.
#: Searching both for the same names finds the first and misses the second.
BREAKS_ON = {
    "depends_on": "it imports this code, so a renamed or removed name breaks it",
    "extends": "it builds on this code, so a changed name or signature breaks it",
    "consumes": "it consumes what this provides, so a changed contract breaks it",
    "exposes": (
        "it calls this over an interface, so a changed route, parameter or "
        "response field breaks it, and it shares no symbol names with this code"
    ),
    "provides": (
        "this calls it over an interface, so what changed here has to match "
        "the route, parameter or field names over there"
    ),
    "tests": (
        "it asserts on this behaviour, so changed output, wording or a renamed "
        "setting breaks it"
    ),
    "contains": "it holds this among its parts",
}


def how_it_is_joined(reached) -> str:
    """Each repository with what would break in it, for the model to aim at."""
    lines = []
    for one in reached:
        meanings = [BREAKS_ON[kind] for kind in one.joined_by if kind in BREAKS_ON]
        joined = ", ".join(one.joined_by) or "reached"
        lines.append(
            f"- {one.name} ({joined})" + (f": {meanings[0]}" if meanings else "")
        )
    return "\n".join(lines)


def searchable_repositories(reached, here: str) -> tuple[str, ...]:
    """The repositories the model may ask about, fixed before it is asked.

    The one being changed, and every system the change reaches that holds code
    of its own. The model picks from this; it cannot widen it.
    """
    names = {one.name for one in reached if one.has_code and one.name}
    if here:
        names.add(here)
    return tuple(sorted(names))


class WhatToLookFor:
    """Asks the review what to search for, and runs what it asks for."""

    def __init__(self, searcher, scope_for, *, rounds: int = MAX_ROUNDS) -> None:
        self._searcher = searcher
        #: Where each repository is searched. The one under review is pinned
        #: to a commit; a sibling has none the reviewer could supply, so it is
        #: searched as it was last read.
        self._scope_for = scope_for
        self._rounds = rounds

    #: Set when the model could not be asked at all. A review that chose not
    #: to search and one that was never able to say look identical in what
    #: comes back, and only the first is a judgement.
    refused: str = ""

    def gather(
        self, provider, *, change, repositories, joined: str = ""
    ) -> tuple[Asked, ...]:
        """Every search the review asked for, with what each one found."""
        self.refused = ""
        if not repositories:
            return ()
        if not hasattr(provider, "ask_with_tools"):
            self.refused = "this model cannot be asked what to look for"
            return ()
        tools = tools_for(repositories)
        about = (
            f"\n\nWhat joins each of them to this repository:\n{joined}"
            if joined
            else ""
        )
        messages = [
            {"role": "user", "content": f"{INSTRUCTIONS}{about}\n\n{change}"},
        ]
        done: list[Asked] = []
        asked_already = False
        for round_number in range(self._rounds):
            try:
                answer = provider.ask_with_tools(
                    messages,
                    tools,
                    purpose="review_search",
                    require=round_number == 0,
                )
            except Exception as error:  # noqa: BLE001 - asking, not reviewing
                logger.warning(
                    "The review could not be asked what to search for: %s", error
                )
                self.refused = f"the model could not be asked: {type(error).__name__}"
                return tuple(done)
            calls = answer.get("tool_calls") or ()
            if not calls:
                left = [
                    name
                    for name in repositories
                    if not any(item.repository == name for item in done)
                ]
                # Asked once and told to stop, a review searches wherever it
                # looked first and leaves the rest reported as unread. Being
                # named is what makes the omission a decision rather than an
                # oversight; it may still answer with nothing.
                if not left or asked_already:
                    return tuple(done)
                asked_already = True
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have not searched "
                            + ", ".join(left)
                            + ". Each is reported as unread, so anything "
                            "broken there goes unmentioned. Search the ones "
                            "this change could affect. Answer with no tool "
                            "call if none of them can be."
                        ),
                    }
                )
                continue
            messages.append(
                {
                    "role": "assistant",
                    "content": answer.get("content") or "",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }
                        for call in calls
                    ],
                }
            )
            for call in calls:
                found = self._run(call, repositories, len(done))
                done.append(found)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": _rendered(found),
                    }
                )
            if len(done) >= MAX_SEARCHES:
                return tuple(done)
        return tuple(done)

    def _run(self, call, repositories, spent: int) -> Asked:
        try:
            asked = json.loads(call.get("arguments") or "{}")
        except ValueError:
            return Asked("", (), unavailable="that request could not be read")
        repository = str(asked.get("repository") or "")
        terms = tuple(str(term) for term in (asked.get("terms") or ()))
        # The list was fixed before the model was asked, and a model that
        # names something outside it is asking for a repository this review
        # was never authorised to read.
        if repository not in repositories:
            return Asked(
                repository,
                terms,
                unavailable="that repository is not one this change reaches",
            )
        if spent >= MAX_SEARCHES:
            return Asked(repository, terms, unavailable="no searches left")
        try:
            query = SearchQuery(self._scope_for(repository), terms[:16])
        except ValueError as error:
            return Asked(repository, terms, unavailable=str(error))
        try:
            result = self._searcher.search(query)
        except Exception as error:  # noqa: BLE001 - one search, not the review
            logger.warning("Searching %s failed: %s", repository, error)
            return Asked(repository, terms, unavailable=f"search failed: {error}")
        if result.unavailable:
            return Asked(repository, terms, unavailable=result.unavailable)
        return Asked(
            repository,
            terms,
            matches=tuple(
                {
                    "path": match.path,
                    "revision": match.revision,
                    "start_line": match.start_line,
                    "end_line": match.end_line,
                    "text": match.text,
                }
                for match in result.matches
            ),
        )


def _rendered(found: Asked) -> str:
    if found.unavailable:
        return json.dumps({"searched": found.repository, "nothing": found.unavailable})
    return json.dumps(
        {
            "searched": found.repository,
            "terms": list(found.terms),
            "matches": [dict(match) for match in found.matches],
        },
        sort_keys=True,
    )
