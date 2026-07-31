"""What can be configured, declared once.

A setting is described here rather than read straight out of the store, so that
everything downstream, the resolver, the API, and the screen a person edits it
on, works from the same declaration. Adding a setting is one entry, not a new
endpoint and a new form field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.models.config import ConfigType

# Where a setting can be given a value. Order matters: the narrowest scope that
# has a value wins, which is what lets a repository depart from its
# organisation without detaching from it.
REPOSITORY = "repository"
ORGANIZATION = "organization"

SCOPE_ORDER: tuple[str, ...] = (REPOSITORY, ORGANIZATION)


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    description: str
    type: str
    default: Any
    # The scopes this setting may be given a value at, narrowest first.
    scopes: tuple[str, ...] = SCOPE_ORDER
    # What the number means, so a screen can say "days" without hardcoding it.
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    # Grouping for presentation, so related settings stay together.
    group: str = "General"

    def validate(self, value: Any) -> Any:
        """Return the value coerced to this setting's type, or raise ValueError."""
        coerced = _coerce(value, self.type)
        if self.choices and str(coerced) not in self.choices:
            raise ValueError(f"{self.key} must be one of {', '.join(self.choices)}")
        if self.minimum is not None and coerced < self.minimum:
            raise ValueError(f"{self.key} cannot be below {self.minimum}")
        if self.maximum is not None and coerced > self.maximum:
            raise ValueError(f"{self.key} cannot be above {self.maximum}")
        return coerced


def _coerce(value: Any, type_: str) -> Any:
    if type_ == ConfigType.BOOL:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes", "on")
    if type_ == ConfigType.INT:
        return int(value)
    if type_ == ConfigType.FLOAT:
        return float(value)
    if type_ == ConfigType.JSON:
        return value
    return str(value)


SETTINGS: tuple[Setting, ...] = (
    Setting(
        key="review.reuse_days",
        label="Reuse a review for",
        description=(
            "How long a generated review is served again for the same revision "
            "before it is generated afresh. A new commit is reviewed again "
            "regardless."
        ),
        type=ConfigType.INT,
        default=7,
        unit="days",
        minimum=0,
        maximum=90,
        group="Review",
    ),
)

BY_KEY: Mapping[str, Setting] = {setting.key: setting for setting in SETTINGS}


def get(key: str) -> Setting:
    setting = BY_KEY.get(key)
    if setting is None:
        raise KeyError(f"Unknown setting: {key}")
    return setting


def for_scope(scope: str) -> tuple[Setting, ...]:
    """The settings that may be given a value at this scope."""
    return tuple(setting for setting in SETTINGS if scope in setting.scopes)


@dataclass(frozen=True)
class Resolved:
    """A value, and where it came from, so a screen can say which."""

    key: str
    value: Any
    # "repository", "organization", or "default".
    source: str
    # The scope the value was read from, absent when it is the default.
    source_id: str | None = None
    setting: Setting | None = field(default=None, compare=False)
