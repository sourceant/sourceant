"""Things a person triggers, as opposed to tools a model decides to call.

How a client offers these differs, so nothing here assumes a syntax.

The fallback rather than the main route: a SKILL.md is portable across clients
and is what ``src/core/skills`` already writes. These cost nothing and need no
install.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.mcp import Surface
from src.core.services import ServiceRegistry, service_registry


@dataclass
class ReviewPrompts:
    """Adds the slash commands that reach this server's review tools."""

    services: ServiceRegistry = field(default=service_registry)

    @property
    def name(self) -> str:
        return "code_reviewer_prompts"

    def add_tools(self, server, surface: Surface | None) -> None:
        if surface is not None and not surface.reaches_checkout:
            return

        @server.prompt(
            name="review",
            title="Review my working tree",
            description=(
                "Review the uncommitted and unpushed work in a checkout "
                "against the skills this team wrote down."
            ),
        )
        def review(repository: str = "") -> str:
            named = f" for the repository named {repository}" if repository else ""
            return (
                f"Review my working tree{named} using the review_working_tree "
                "tool from SourceAnt.\n\n"
                "It answers with a link rather than the findings, because the "
                "reading happens after the call returns. Give me that link, and "
                "say in one line what is being reviewed against what. Do not "
                "wait for the review or poll for it.\n\n"
                "If no repository is named and more than one is registered, ask "
                "me which one rather than guessing."
            )

        @server.prompt(
            name="context",
            title="What SourceAnt knows about this code",
            description=(
                "Read the indexed graph and the decisions recorded against a "
                "repository, instead of grepping for them."
            ),
        )
        def context(about: str = "") -> str:
            asked = about or "the part of the code I am working in"
            return (
                f"Tell me what SourceAnt already knows about {asked}.\n\n"
                "Use search_code and trace_code to find the structure, and "
                "get_context to collect the decisions, requirements and "
                "findings recorded against it. Prefer those over reading files: "
                "the point of the index is that it answers in one call what "
                "reading the tree answers in fifty.\n\n"
                "Lead with what is recorded, then what the code says. Say "
                "plainly where nothing is recorded rather than filling the gap "
                "from the code."
            )

        @server.prompt(
            name="remember",
            title="Write down what we just decided",
            description=(
                "Record a decision, convention or constraint so the next "
                "session and the next review both know it."
            ),
        )
        def remember(decision: str = "") -> str:
            said = f":\n\n{decision}" if decision else "."
            return (
                f"Write down what we decided{said}\n\n"
                "Use put_knowledge to record it against this repository. Give "
                "it a kind (decision, convention, constraint), a summary "
                "somebody who was not here would understand, and the reason in "
                "its properties under 'why'.\n\n"
                "Show me what you are about to record and let me correct it "
                "before writing."
            )
