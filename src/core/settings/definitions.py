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
    # Whether the value is a credential. One is written like any other setting
    # and never read back: a screen shows whether it is set, and anything
    # answering with it would put it in a log the first time somebody debugged
    # the screen.
    secret: bool = False
    # Whether the value is several of something rather than one thing. Stored
    # one to a line; drawn as a list somebody adds to and removes from, because
    # a box of lines is a text editor pretending to be a list.
    listed: bool = False

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
        group="KnowledgeObject initialization",
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
        group="KnowledgeObject initialization",
    ),
    Setting(
        key="initialization.evidence_character_limit",
        label="Evidence character budget",
        description="Maximum evidence characters supplied in one initialization stage.",
        type=ConfigType.INT,
        default=60_000,
        unit="characters",
        minimum=1_000,
        maximum=100_000,
        scopes=(REPOSITORY, ORGANIZATION),
        group="KnowledgeObject initialization",
    ),
    Setting(
        key="initialization.community_limit",
        label="Maximum parts read separately",
        description=(
            "How many parts of related code a large repository is read in. "
            "Each is given the whole evidence budget and read on its own, so "
            "raising this reads more of the repository and costs proportionally "
            "more. One reads the repository as a single piece."
        ),
        type=ConfigType.INT,
        default=25,
        minimum=1,
        maximum=100,
        scopes=(REPOSITORY, ORGANIZATION),
        group="KnowledgeObject initialization",
    ),
    Setting(
        key="initialization.excluded_paths",
        label="Paths left out of the index",
        description=(
            "Path patterns the index does not report. A pattern matches a whole "
            'path segment, so ".github" also leaves everything under it out. '
            "The index still holds them; what reads the index stops seeing them."
        ),
        type=ConfigType.JSON,
        default=(".github", ".codebase-memory"),
        scopes=(REPOSITORY, ORGANIZATION),
        group="KnowledgeObject initialization",
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
        group="KnowledgeObject initialization",
    ),
    # Whose model, and whose bill. Reading a repository is deterministic and
    # needs none of this; anything that proposes rather than reads does, and it
    # stays off until somebody says which model to ask.
    Setting(
        key="model.name",
        label="Model",
        description=(
            "The model asked when something has to be proposed rather than "
            "read. Named the way the provider names it, for example "
            "anthropic/claude-sonnet-4-5 or openai/gpt-4o."
        ),
        type=ConfigType.STRING,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default="",
        group="Model",
    ),
    Setting(
        key="model.api_key",
        label="API key",
        description=(
            "The key for that provider. It is kept on this machine, sent to "
            "that provider and nowhere else, and never read back."
        ),
        type=ConfigType.STRING,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default="",
        group="Model",
        secret=True,
    ),
    Setting(
        key="model.base_url",
        label="Endpoint",
        description=(
            "Where to reach the model, for a provider that is not the default "
            "one or a model running on this machine. Left empty otherwise."
        ),
        type=ConfigType.STRING,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default="",
        group="Model",
    ),
    # Skills are read from the folders each coding agent keeps them in, and
    # from this product's own. People keep them elsewhere too: in a repository
    # of their own, in a plugin, in a package of a monorepo. Nothing can guess
    # those, so they are named.
    Setting(
        key="skills.paths",
        label="Extra places to look",
        description=(
            "Directories to read skills from, one to a line, on top of the "
            "folders your coding agents already keep them in. A path to a "
            "folder of skills, where each skill is a directory holding a "
            "SKILL.md."
        ),
        type=ConfigType.STRING,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default="",
        group="Skills",
        listed=True,
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
