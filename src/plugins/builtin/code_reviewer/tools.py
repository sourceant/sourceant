"""The MCP tool that reviews a checkout."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import Thread
from typing import Any

from src.core.mcp import Surface
from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger


@dataclass
class ReviewTools:
    """Adds ``review_working_tree`` where the server can reach a checkout."""

    services: ServiceRegistry = field(default=service_registry)

    @property
    def name(self) -> str:
        return "code_reviewer"

    def add_tools(self, server, surface: Surface | None) -> None:
        # A hosted server has no disk to read, and a tool that always refuses
        # is worse than one that is not advertised.
        if surface is not None and not surface.reaches_checkout:
            return

        @server.tool(
            name="review_working_tree",
            description=(
                "Read the uncommitted and unpushed work in a checkout against "
                "the skills its team wrote down, and answer with a link a "
                "person can open to see what it found. The reading happens "
                "after this answers, so open the link rather than waiting."
            ),
            structured_output=True,
        )
        def review_working_tree(repository: str, title: str = "") -> dict[str, Any]:
            """Ask for a review, and hand back where to read it.

            A link rather than the findings: what asks for a review is rarely
            what acts on it.
            """
            started = self._start(repository, title)
            return {
                "id": started["id"],
                "repository": started["repository"],
                "status": started["status"],
                "url": started.get("url") or started.get("path", ""),
                "say": (
                    "Reading has started. Open the link to see what it found; "
                    "it keeps working whether or not anybody is watching."
                ),
            }

    def _start(self, repository: str, title: str) -> dict:
        """Hand the work to the agent, or do it here if it cannot be reached.

        Preferably the agent: this is often a stdio process that lives exactly
        as long as the client holding it, and a review outlives that. A thread
        started here dies with the process and leaves a review reading
        "running" for ever.

        Reaching it is a different address from the one a person clicks. This
        may be inside a container, where loopback is the container.
        """
        agent = _reachable_agent()
        if agent:
            try:
                return _asked_of_the_agent(agent, repository, title)
            except ValueError as unreachable:
                if "not answering" not in str(unreachable):
                    raise
                logger.warning(f"{unreachable}. Reading it here instead.")
        return self._run_here(repository, title)

    def _run_here(self, repository: str, title: str) -> dict:
        from src.core.review import ReviewRecord, named, working_tree_reviewer

        judge = working_tree_reviewer(self.services)
        if judge is None:
            raise ValueError("This server does not review working trees")

        reviews = _kept()
        identifier = named()
        started = reviews.put(
            ReviewRecord(id=identifier, repository=repository, title=title)
        )
        Thread(
            target=_read_and_keep,
            args=(judge, reviews, identifier, repository, title),
            daemon=True,
        ).start()

        answer = _payload(started)
        answer["url"] = _where(answer["path"])
        return answer


def _kept():
    from src.config.db import get_engine
    from src.core.review import SQLReviewStore, review_store

    kept = review_store()
    if kept is not None:
        return kept
    engine = get_engine()
    if engine is None:
        raise ValueError("There is nowhere to keep a review")
    return SQLReviewStore(engine, create_schema=True)


def _read_and_keep(judge, reviews, identifier, repository, title) -> None:
    from src.core.review import DONE, FAILED, ReviewRecord, now

    try:
        answer = judge.review(repository=repository, title=title)
    except Exception as error:  # noqa: BLE001 - what went wrong is the answer
        reviews.put(
            ReviewRecord(
                id=identifier,
                repository=repository,
                status=FAILED,
                error=getattr(error, "detail", None) or str(error),
                title=title,
                finished=now(),
            )
        )
        return
    reviews.put(
        ReviewRecord(
            id=identifier,
            repository=repository,
            status=DONE,
            answer=answer,
            title=title,
            finished=now(),
        )
    )


def _payload(review) -> dict:
    return {
        "id": review.id,
        "repository": review.repository,
        "status": review.status,
        "title": review.title,
        "path": f"/reviews/{review.id}",
    }


def _where(path: str) -> str:
    """A link a person can open, which is not the address anything reaches."""
    base = os.getenv("SOURCEANT_UI_URL", "").rstrip("/")
    return f"{base}{path}" if base else path


def _reachable_agent() -> str:
    """Where the agent answers from here, which may not be where it is clicked."""
    for name in ("SOURCEANT_AGENT_URL", "SOURCEANT_UI_URL"):
        found = os.getenv(name, "").rstrip("/")
        if found:
            return found
    return ""


def _asked_of_the_agent(agent: str, repository: str, title: str) -> dict:
    """Hand the work to the process that will outlive this one."""
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{agent}/api/reviews",
        method="POST",
        data=json.dumps(
            {"repository": repository, "title": title, "use_model": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            started = json.load(answer)
    except urllib.error.HTTPError as refused:
        raise ValueError(refused.read().decode(errors="replace")[:500]) from refused
    except OSError as error:
        # The agent is how this reaches anything. Saying so beats a review
        # that never finishes.
        raise ValueError(f"The SourceAnt agent is not answering at {agent}") from error

    # The clickable address where one is configured, otherwise wherever this
    # reached the agent, which is at least somewhere.
    clickable = os.getenv("SOURCEANT_UI_URL", "").rstrip("/") or agent
    started["url"] = f"{clickable}/reviews/{started['id']}"
    return started
