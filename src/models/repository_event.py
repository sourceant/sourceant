from sqlmodel import Field
from typing import Optional
from sqlalchemy import Column, JSON
from src.models.base_model import BaseModel


class RepositoryEvent(BaseModel, table=True):
    __tablename__ = "repository_events"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    provider: str = Field(..., index=True)
    type: str
    action: str
    number: Optional[int] = None
    repository: str
    url: Optional[str] = None
    title: Optional[str] = None
    payload: dict = Field(default={}, sa_column=Column(JSON))

    def __repr__(self):
        return f"<RepositoryEvent(provider={self.provider}, type={self.type}, action={self.action})>"
