from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import Field

from src.models.base_model import BaseModel


class CachedReview(BaseModel, table=True):
    __tablename__ = "cached_reviews"

    id: Optional[int] = Field(default=None, primary_key=True)
    #: Repository, pull request and revision together. A new revision misses
    #: rather than needing anything invalidated.
    key: str = Field(max_length=255, unique=True, index=True)
    payload: str = Field(sa_column=Column(Text, nullable=False))
    expires_at: datetime = Field(index=True)
