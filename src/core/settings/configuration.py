"""Reading settings for one piece of work, from one place."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional

from src.core.scope import Scope
from src.core.settings.definitions import Resolved
from src.core.settings.resolver import UNSTATED, resolve


@dataclass(frozen=True)
class Configuration:
    """Every setting, read at the scopes one piece of work belongs to.

    Named once here rather than at each read: a read that leaves a scope out
    resolves as though that scope named nothing.
    """

    repository: Optional[str] = None
    organization: Optional[str] = None
    user: Optional[str] = None
    #: Left unnamed, the workspace holding the repository is looked up on every
    #: read. `with_workspace` settles it once for a caller doing several.
    workspace: Optional[str] = None

    @classmethod
    def from_scope(cls, scope: Optional[Scope]) -> "Configuration":
        """The scopes a change carries."""
        if scope is None:
            return cls()
        return cls(
            repository=scope.get("repository"),
            organization=scope.get("organization"),
            user=scope.get("user"),
            workspace=scope.get("workspace"),
        )

    def with_workspace(self) -> "Configuration":
        """The workspace holding the repository, settled here rather than on
        every read, because that lookup goes to the database."""
        if self.workspace or not self.repository:
            return self
        from src.core.workspace import workspace_holding

        return replace(self, workspace=workspace_holding(self.repository))

    def with_user(self, user: str) -> "Configuration":
        return self if self.user else replace(self, user=user)

    def resolved(self, key: str) -> Resolved:
        return resolve(
            key,
            repository=self.repository,
            organization=self.organization,
            user=self.user,
            workspace=self.workspace if self.workspace else UNSTATED,
        )

    def value(self, key: str) -> Any:
        return self.resolved(key).value

    def attribution(self) -> dict[str, Optional[str]]:
        """What a call made under this is recorded against."""
        return {
            "repository": self.repository,
            "organization": self.organization,
            "user": self.user,
            "workspace": self.workspace,
        }
