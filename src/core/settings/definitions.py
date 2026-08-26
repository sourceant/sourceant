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
USER = "user"
REPOSITORY = "repository"
ORGANIZATION = "organization"

SCOPE_ORDER: tuple[str, ...] = (USER, REPOSITORY, ORGANIZATION)


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    description: str
    type: str
    scopes: tuple[str, ...]
    default: Any
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
        scopes=(REPOSITORY, ORGANIZATION),
        default=7,
        unit="days",
        minimum=0,
        maximum=90,
        group="Review",
    ),
    Setting(
        key="review.structural_context_file_limit",
        label="Structural context files",
        description=(
            "Maximum changed source files read to build temporary structural "
            "review context. The complete diff remains available to the review."
        ),
        type=ConfigType.INT,
        scopes=(REPOSITORY, ORGANIZATION),
        default=20,
        unit="files",
        minimum=1,
        maximum=100,
        group="Review",
    ),
    Setting(
        key="initialization.candidate_limit",
        label="Maximum knowledge proposals",
        description="Maximum proposals considered during repository initialization.",
        type=ConfigType.INT,
        default=20,
        minimum=1,
        maximum=50,
        scopes=(REPOSITORY, ORGANIZATION),
        group="Knowledge initialization",
    ),
    Setting(
        key="initialization.evidence_limit",
        label="Maximum evidence items",
        description="Maximum evidence items supplied during repository initialization.",
        type=ConfigType.INT,
        default=20,
        minimum=1,
        maximum=100,
        scopes=(REPOSITORY, ORGANIZATION),
        group="Knowledge initialization",
    ),
    Setting(
        key="initialization.evidence_character_limit",
        label="Evidence character budget",
        description="Maximum evidence characters supplied in one initialization stage.",
        type=ConfigType.INT,
        default=20_000,
        unit="characters",
        minimum=1_000,
        maximum=100_000,
        scopes=(REPOSITORY, ORGANIZATION),
        group="Knowledge initialization",
    ),
    Setting(
        key="initialization.community_limit",
        label="Maximum parts read separately",
        description=(
            "How many clusters of related code a large repository is read in. "
            "Each is given the whole evidence budget and read on its own, so "
            "raising this reads more of the repository and costs proportionally "
            "more. One reads the repository as a single piece."
        ),
        type=ConfigType.INT,
        default=10,
        minimum=1,
        maximum=100,
        scopes=(REPOSITORY, ORGANIZATION),
        group="Knowledge initialization",
    ),
    Setting(
        key="initialization.investigation_limit",
        label="Maximum follow-up investigations",
        description="Maximum graph identities investigated after initial retrieval.",
        type=ConfigType.INT,
        default=12,
        minimum=0,
        maximum=50,
        scopes=(REPOSITORY, ORGANIZATION),
        group="Knowledge initialization",
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
    # "user", "repository", "organization", or "default".
    source: str
    # The scope the value was read from, absent when it is the default.
    source_id: str | None = None
    setting: Setting | None = field(default=None, compare=False)
