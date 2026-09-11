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

from src.core.search import CodeTextQuery
from src.utils.logger import logger

from .models import Asked, MAX_ROUNDS, MAX_SEARCHES

SEARCH_CODE = "search_code"

INSTRUCTIONS = (
    "You are about to review the change below. Before you do, decide what is "
    "worth looking for in the repositories it reaches, and search for it.\n\n"
    "Search for the names a reader would have to find to know whether this "
    "change breaks something: the thing being renamed or removed under its "
    "old name, the endpoint under the name a client would call it by, the "
    "symbol whose signature moved. A name that appears nowhere in the diff is "
    "often the one worth searching for.\n\n"
    "Search terms are words. A term must be three or more letters and digits "
    "with no punctuation, so split an endpoint or a snake_case name into its "
    "parts. Ask for what you need and stop; you may search up to "
    f"{MAX_SEARCHES} times in total. Answer with no tool call when you have "
    "enough, or immediately if nothing here reaches other code."
)


def tools_for(repositories: tuple[str, ...]) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": SEARCH_CODE,
                "description": (
                    "Find where a name is defined, called or used in one of "
                    "the repositories this change reaches."
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
                            "description": "Between one and sixteen words.",
                        },
                    },
                    "required": ["repository", "terms"],
                },
            },
        }
    ]


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

    def gather(self, provider, *, change, repositories) -> tuple[Asked, ...]:
        """Every search the review asked for, with what each one found."""
        if not repositories or not hasattr(provider, "ask_with_tools"):
            return ()
        tools = tools_for(repositories)
        messages = [
            {"role": "user", "content": f"{INSTRUCTIONS}\n\n{change}"},
        ]
        done: list[Asked] = []
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
                return tuple(done)
            calls = answer.get("tool_calls") or ()
            if not calls:
                return tuple(done)
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
            query = CodeTextQuery(self._scope_for(repository), terms[:16])
        except ValueError as error:
            return Asked(repository, terms, unavailable=str(error))
        try:
            result = self._searcher.search_text(query)
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
