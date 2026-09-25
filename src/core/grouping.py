"""Where grouping is put together, resolved the same way wherever it is asked for.

``ServiceRegistry.register`` allows one provider per interface, so core cannot
pre-register alongside a plugin and resolution has to happen per call. The same
goes for what can be grouped: a plugin contributes its own types, and core adds
requirements, which is the one kind it owns itself.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from src.config.db import get_engine
from src.core.groups import (
    CheckedGroups,
    Groupable,
    GroupsRepository,
    SQLGroupsRepository,
)
from src.core.requirements.grouping import TYPE as REQUIREMENT, GroupableRequirements
from src.core.requirements.interfaces import RequirementsReader, RequirementsRepository
from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger

_fallback: Optional[GroupsRepository] = None


def groupables(
    services: ServiceRegistry = service_registry,
    requirements: Optional[RequirementsReader] = None,
) -> tuple[Groupable, ...]:
    """Every type of thing that can be filed, contributed ones first.

    A caller that already holds the requirements store passes it, so that what
    a group counts and what a review reads are the same rows.
    """
    contributed = services.contributions(Groupable)
    if any(item.type == REQUIREMENT for item in contributed):
        return contributed
    reader = requirements if requirements is not None else _requirements(services)
    if reader is None:
        return contributed
    return (*contributed, GroupableRequirements(reader))


def groups(
    services: ServiceRegistry = service_registry,
    requirements: Optional[RequirementsReader] = None,
) -> Optional[CheckedGroups]:
    """The group store, with whoever owns the things it files attached.

    None when there is nowhere to keep them, the way requirements answer when
    the engine is missing, so a caller can say so rather than fail late.
    """
    store = _store(services)
    if store is None:
        return None
    return CheckedGroups(store, groupables(services, requirements))


def _store(services: ServiceRegistry) -> Optional[GroupsRepository]:
    """The registered store, else core's own, built once.

    Built once because this is resolved per call, and a request that made a new
    store each time would pay for one on every page of a list.
    """
    global _fallback
    try:
        return services.resolve(GroupsRepository)
    except LookupError:
        pass
    if _fallback is None:
        engine = get_engine()
        _fallback = None if engine is None else SQLGroupsRepository(engine)
    return _fallback


def _requirements(services: ServiceRegistry) -> Optional[RequirementsReader]:
    try:
        return services.resolve(RequirementsRepository)
    except LookupError:
        pass
    engine = get_engine()
    if engine is None:
        return None
    try:
        from src.core.requirements.sql import SQLRequirementsRepository

        return SQLRequirementsRepository(engine)
    except SQLAlchemyError as error:
        logger.warning(f"Requirements cannot be read for grouping: {error}")
        return None
