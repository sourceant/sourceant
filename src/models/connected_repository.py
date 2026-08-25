from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


class ConnectedRepository(SQLModel, table=True):
    """Which repositories a workspace has taken on.

    Connecting belongs to the workspace, not to whoever happened to click. A
    teammate joining sees what the team connected, someone leaving takes nothing
    with them, and the same person in two workspaces sees two different sets,
    which is what makes switching mean anything.
    """

    __tablename__ = "connected_repositories"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "repository_id", name="uq_workspace_repository"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: str = Field(index=True, max_length=255)
    repository_id: int = Field(foreign_key="repositories.id", index=True)
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
