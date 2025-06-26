from sqlmodel import Field
from typing import Optional
from src.models.base_model import BaseModel


class PullRequest(BaseModel, table=True):
    __tablename__ = "pull_requests"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    provider: str = Field(..., index=True)
    number: int
    title: str
    body: Optional[str] = None
    url: str = Field(..., unique=True)
    state: str
    locked: bool
    merged: bool
    draft: bool
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None
    merged_at: Optional[str] = None
    user: str
    head_ref: str
    base_ref: str
    commits: int
    additions: int
    deletions: int
    changed_files: int
    repository_id: int

    def __repr__(self):
        return f"<PullRequest(provider={self.provider}, number={self.number}, title={self.title})>"
