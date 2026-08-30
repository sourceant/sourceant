"""Resolve a setting through user, repository, and organization scopes.

A user value wins when supplied. Repository values otherwise inherit from their
organization, and an organization inherits the shipped default. The answer carries
its source so a screen can show whether it is set here or inherited.
"""

from __future__ import annotations

from typing import Any, Optional

from src.core.settings.definitions import (
    ORGANIZATION,
    REPOSITORY,
    USER,
    Resolved,
    Setting,
    for_scope,
    get,
)
from src.config.settings import STATELESS_MODE
from src.models.config import Config
from src.utils.logger import logger


def organization_of(repository: str) -> Optional[str]:
    """The organisation a repository belongs to, from its full name."""
    owner, separator, _ = repository.partition("/")
    return owner if separator and owner else None


def _stored(setting: Setting, scope: str, scope_id: str) -> Any:
    if scope not in setting.scopes:
        return None
    # Nothing is stored without a database, and attempting the read logs an
    # error for a condition that is normal in a stateless deployment.
    if STATELESS_MODE:
        return None
    try:
        raw = Config.get_value(scope, scope_id, setting.key)
    except Exception as error:
        # A setting that cannot be read falls back rather than failing the
        # request that needed it.
        logger.warning(f"Could not read {setting.key} for {scope} {scope_id}: {error}")
        return None
    if raw is None:
        return None
    try:
        return setting.validate(raw)
    except ValueError as error:
        logger.warning(
            f"Ignoring invalid {setting.key} for {scope} {scope_id}: {error}"
        )
        return None


def resolve(
    key: str,
    repository: Optional[str] = None,
    organization: Optional[str] = None,
    user: Optional[str] = None,
) -> Resolved:
    """Resolve one setting, narrowest scope first."""
    setting = get(key)

    if user:
        value = _stored(setting, USER, user)
        if value is not None:
            return Resolved(key, value, USER, user, setting)

    if repository:
        value = _stored(setting, REPOSITORY, repository)
        if value is not None:
            return Resolved(key, value, REPOSITORY, repository, setting)

    owner = organization or (organization_of(repository) if repository else None)
    if owner:
        value = _stored(setting, ORGANIZATION, owner)
        if value is not None:
            return Resolved(key, value, ORGANIZATION, owner, setting)

    return Resolved(key, setting.default, "default", None, setting)


def value_of(
    key: str,
    repository: Optional[str] = None,
    organization: Optional[str] = None,
    user: Optional[str] = None,
) -> Any:
    """The resolved value alone, for callers that do not care where it came from."""
    return resolve(key, repository, organization, user).value


def resolve_all(
    repository: Optional[str] = None,
    organization: Optional[str] = None,
    user: Optional[str] = None,
) -> tuple[Resolved, ...]:
    """Every setting that applies to this scope, resolved."""
    scope = USER if user else REPOSITORY if repository else ORGANIZATION
    return tuple(
        resolve(setting.key, repository, organization, user)
        for setting in for_scope(scope)
    )


def set_value(scope: str, scope_id: str, key: str, value: Any) -> Resolved:
    """Give a setting a value at one scope. Raises ValueError if it is not valid."""
    setting = get(key)
    if scope not in setting.scopes:
        raise ValueError(f"{key} cannot be set at the {scope} level")
    coerced = setting.validate(value)
    Config.set_value(scope, scope_id, key, coerced, setting.type)
    return Resolved(key, coerced, scope, scope_id, setting)


def clear_value(scope: str, scope_id: str, key: str) -> None:
    """Remove a value so the scope goes back to inheriting."""
    setting = get(key)
    Config.delete_value(scope, scope_id, setting.key)
