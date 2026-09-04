"""What can be configured, declared once.

A setting is described here rather than read straight out of the store, so that
everything downstream, the resolver, the API, and the screen a person edits it
on, works from the same declaration. Adding a setting is one entry, not a new
endpoint and a new form field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.config.settings import DEFAULT_TOKEN_LIMIT
from src.models.config import ConfigType

# Where a setting can be given a value. Order matters: the narrowest scope that
# has a value wins, which is what lets a repository depart from its
# organisation without detaching from it.
#
# An organisation here is the owner half of a repository's full name, so it is
# whoever holds the namespace on the forge rather than whoever is paying. A
# workspace is the account, and two of them can hold the same repository.
USER = "user"
REPOSITORY = "repository"
WORKSPACE = "workspace"
ORGANIZATION = "organization"

SCOPE_ORDER: tuple[str, ...] = (USER, REPOSITORY, WORKSPACE, ORGANIZATION)


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
        key="review.reading_budget",
        label="Read at once",
        description=(
            "How much of a change is read in one go, in tokens. A larger "
            "change is read in parts of this size and the parts are put "
            "together. This is not how much the model can accept: it is where "
            "a review stops finding things, which is far below that."
        ),
        type=ConfigType.INT,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default=15_000,
        unit="tokens",
        minimum=2_000,
        maximum=200_000,
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
        key="review.remember_findings",
        label="Remember what a review said",
        description=(
            "Keep each thing a review says, so the next one knows it has said "
            "it before and anything dismissed stays dismissed. A finding is "
            "recognised by what it says and what it proposes, not by where it "
            "is, but a reviewer that rewords itself will still raise the odd "
            "duplicate. Off until you want that trade."
        ),
        type=ConfigType.BOOL,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default=False,
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
        scopes=(USER, REPOSITORY, WORKSPACE),
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
        scopes=(USER, WORKSPACE),
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
        scopes=(USER, REPOSITORY, WORKSPACE),
        default="",
        group="Model",
    ),
    Setting(
        key="model.token_limit",
        label="How much it can read at once",
        description=(
            "How many tokens the chosen model accepts. A larger change is read "
            "a file at a time instead of whole."
        ),
        type=ConfigType.INT,
        scopes=(USER, REPOSITORY, WORKSPACE),
        default=DEFAULT_TOKEN_LIMIT,
        group="Model",
    ),
    # A repository read once is a repository that answers about last month.
    # Reading again is cheap: unchanged files are recognised and skipped.
    Setting(
        key="index.every",
        label="Read repositories again every",
        description=(
            "How often the folders on this machine are read again, in "
            "minutes. Only what changed is read, so this costs close to "
            "nothing. Zero turns it off and leaves reading to the button."
        ),
        type=ConfigType.INT,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default=60,
        minimum=0,
        maximum=10_080,
        unit="minutes",
        group="Schedule",
    ),
    # Asking a model costs money every time, so this is off until somebody
    # decides the answers are worth it.
    Setting(
        key="knowledge.every",
        label="Look for new knowledge every",
        description=(
            "How often a repository is read again for what it states about "
            "itself, in minutes, and asked of a model where one is "
            "configured. Zero, the default, means only when you ask."
        ),
        type=ConfigType.INT,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default=0,
        minimum=0,
        maximum=10_080,
        unit="minutes",
        group="Schedule",
    ),
    # Nothing arrives agreed. A person looking at every proposal is the point
    # where most of this stops being used, so a team that trusts the reading
    # can say how sure is sure enough to skip that.
    Setting(
        key="knowledge.accept_above",
        label="Accept on its own above",
        description=(
            "How sure a proposal has to be before it is accepted without "
            "anybody looking, from 0 to 1. Zero, the default, means every "
            "proposal waits for a person. What a repository plainly states "
            "about itself is quoted rather than inferred and counts as one."
        ),
        type=ConfigType.FLOAT,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default=0.0,
        minimum=0.0,
        maximum=1.0,
        group="Knowledge",
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
    # "user", "repository", "workspace", "organization", or "default".
    source: str
    # The scope the value was read from, absent when it is the default.
    source_id: str | None = None
    setting: Setting | None = field(default=None, compare=False)
