from typing import TYPE_CHECKING, List, Optional

from sqlmodel import Field, Relationship

from src.models.base_model import BaseModel
from src.models.connected_repository import ConnectedRepository

if TYPE_CHECKING:
    from src.models.workspace import Workspace


class Repository(BaseModel, table=True):
    __tablename__ = "repositories"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    provider: str = Field(..., index=True)
    name: str
    full_name: str
    url: str
    description: Optional[str] = None
    private: bool
    archived: bool
    visibility: str
    owner: str
    owner_type: str
    language: Optional[str] = None
    default_branch: str

    workspaces: List["Workspace"] = Relationship(
        back_populates="repositories", link_model=ConnectedRepository
    )

    def __repr__(self):
        return f"<Repository(provider={self.provider}, name={self.name}, full_name={self.full_name})>"
