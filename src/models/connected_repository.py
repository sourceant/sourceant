from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


class ConnectedRepository(SQLModel, table=True):
    __tablename__ = "connected_repositories"
    __table_args__ = (
        UniqueConstraint("user_id", "repository_id", name="uq_user_repository"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    repository_id: int = Field(foreign_key="repositories.id", index=True)
    connected_at: datetime = Field(default_factory=datetime.utcnow)
