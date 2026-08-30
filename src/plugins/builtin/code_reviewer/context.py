"""What a reviewer is told before it starts: recorded decisions, what the
change reaches, and the code around it.

Assembled the same way whatever the change came from.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from src.core.change_context import (
    ChangeContextResolver,
    ChangedFile,
    ChangeSet,
    DefaultChangeContextResolver,
)
from src.core.code_index import CodeIndexReader
from src.core.knowledge import (
    KnowledgeReader,
    KnowledgeSelector,
    LinkedKnowledgeSelector,
)
from src.core.impact import ChangeImpactResolver
from src.core.requirements import (
    LinkedRequirementSelector,
    RequirementSelector,
    RequirementsReader,
)
from src.core.review_context import (
    DefaultReviewCodeContextPreparer,
    merge_review_code_contexts,
)
from src.core.scope import Scope
from sqlalchemy.exc import SQLAlchemyError

from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger


def change_context_resolver(
    services: ServiceRegistry, durable_code
) -> ChangeContextResolver:
    """Everything recorded that bears on one change, in one call.

    A deployment that knows better registers its own and keeps the rest.
    """
    try:
        return services.resolve(ChangeContextResolver)
    except LookupError:
        pass
    return DefaultChangeContextResolver(
        code=durable_code,
        knowledge=knowledge_selector(services),
        requirements=requirement_selector(services),
        impact=impact_preparer(services),
    )


def knowledge_selector(services: ServiceRegistry):
    try:
        return services.resolve(KnowledgeSelector)
    except LookupError:
        pass
    try:
        reader = services.resolve(KnowledgeReader)
    except LookupError:
        reader = core_knowledge()
    return LinkedKnowledgeSelector(reader) if reader is not None else None


def requirement_selector(services: ServiceRegistry):
    try:
        return services.resolve(RequirementSelector)
    except LookupError:
        pass
    try:
        reader = services.resolve(RequirementsReader)
    except LookupError:
        reader = core_requirements()
    return LinkedRequirementSelector(reader) if reader is not None else None


def impact_preparer(services: ServiceRegistry):
    try:
        return services.resolve(ChangeImpactResolver)
    except LookupError:
        return core_impact_preparer(services)


def durable_index(services: ServiceRegistry = service_registry):
    """The index this deployment keeps, or core's own."""
    try:
        return services.resolve(CodeIndexReader)
    except LookupError:
        return core_code_index()


def prepare_code_context(
    readers: tuple[CodeIndexReader | None, CodeIndexReader] | None,
    repository: str,
    revision: str,
    paths: List[str],
    *,
    scope: Scope | None = None,
    read_content: Callable[[str], str | None] | None = None,
    file_limit: int = 20,
) -> str | None:
    """The bounded graph around what changed, from every index on hand."""
    if readers is None:
        return None
    contexts = []
    for reader in readers:
        if reader is None:
            continue
        try:
            context = DefaultReviewCodeContextPreparer(
                reader,
                read_content=read_content,
                file_limit=file_limit,
            ).prepare(
                repository=repository,
                revision=revision,
                paths=paths,
                scope=scope,
            )
        except (OSError, RuntimeError, ValueError, SQLAlchemyError):
            continue
        if context:
            contexts.append(context)
    merged = merge_review_code_contexts(contexts)
    return merged.content if merged else None


def requirements_section(known) -> Optional[str]:
    if known is None or not known.requirements:
        return None
    lines = [
        "## Requirements This Change Is Answerable To",
        "Judge the change against these as well as against how it is written.",
        "",
    ]
    for item in known.requirements:
        lines.append(f"- {item.id} ({item.status}): {item.summary}")
    lines.append("")
    return "\n".join(lines)


def knowledge_section(known) -> Optional[str]:
    if known is None or not known.knowledge:
        return None
    lines = [
        "## Decisions And Rules Governing This Code",
        "Recorded by the team and still standing. A change that breaks one of "
        "these is a defect even when the code reads correctly.",
        "",
    ]
    for item in known.knowledge:
        lines.append(f"- {item.id} ({item.kind}, {item.status}): {item.summary}")
    lines.append("")
    return "\n".join(lines)


def impact_section(known) -> Optional[str]:
    if known is None or known.impact is None or not known.impact.findings:
        return None
    lines = [
        "## What This Change Reaches",
        "Parts of the wider system that depend on what is being changed. A "
        "finding marked uncertain is a question to raise, not a fact to assert.",
        "",
    ]
    for finding in known.impact.findings:
        certainty = "certain" if finding.certain else "uncertain"
        reached = ", ".join(finding.topology_entity_ids)
        lines.append(f"- {finding.summary} ({certainty}, reaches {reached})")
    lines.append("")
    return "\n".join(lines)


def known_for(
    changes: ChangeSet, services: ServiceRegistry, durable_code
) -> Any | None:
    """What is recorded about the files this change touches."""
    if not changes.files:
        return None
    return change_context_resolver(services, durable_code).resolve(changes)


def core_impact_preparer(services: ServiceRegistry):
    from src.config.db import get_engine
    from src.core.impact import (
        DefaultChangeImpactResolver,
        SQLCompatibilityCheckRepository,
        SQLImpactSeedRepository,
    )
    from src.core.topology import SQLTopologyRepository, TopologyReader

    from sqlalchemy.exc import SQLAlchemyError

    engine = get_engine()
    if engine is None:
        return None
    try:
        try:
            topology = services.resolve(TopologyReader)
        except LookupError:
            topology = SQLTopologyRepository(engine)
        return DefaultChangeImpactResolver(
            seeds=SQLImpactSeedRepository(engine),
            topology=topology,
            compatibility=SQLCompatibilityCheckRepository(engine),
        )
    except SQLAlchemyError as error:
        logger.warning(f"Reviewing without what a change reaches: {error}")
        return None


def _reachable(build):
    """A store, or None where the database cannot be reached.

    These connect on construction. An unreachable store is treated as absent,
    so a review reads less rather than failing.
    """
    from sqlalchemy.exc import SQLAlchemyError
    from src.config.db import get_engine

    engine = get_engine()
    if engine is None:
        return None
    try:
        return build(engine)
    except SQLAlchemyError as error:
        logger.warning(f"Reviewing without a store that could not be reached: {error}")
        return None


def core_knowledge():
    from src.core.knowledge import SQLKnowledgeRepository

    return _reachable(SQLKnowledgeRepository)


def core_requirements():
    from src.core.requirements import SQLRequirementsRepository

    return _reachable(SQLRequirementsRepository)


def core_code_index():
    from src.core.code_index import SQLCodeIndexRepository

    return _reachable(SQLCodeIndexRepository)


def changed_files(parsed_files) -> tuple[ChangedFile, ...]:
    """The files a diff touches, each carrying the patch that touched it.

    The patch travels on the file so a selector matching on content has
    something to match. Binary files are included with no patch: a change made
    only of binaries is still a change, and dropping them leaves nothing to
    review.
    """
    return tuple(
        ChangedFile(
            path=parsed_file.file_path,
            change=_change_of(parsed_file),
            properties=(
                {"binary": True}
                if parsed_file.is_binary_file
                else {"patch": parsed_file.diff_text}
            ),
        )
        for parsed_file in parsed_files
        if parsed_file.file_path
    )


def _change_of(parsed_file) -> str:
    patch = getattr(parsed_file, "_patched_file", None)
    if getattr(patch, "is_added_file", False):
        return "added"
    if getattr(patch, "is_removed_file", False):
        return "removed"
    if getattr(patch, "is_rename", False):
        return "renamed"
    return "modified"
