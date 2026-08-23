from sqlmodel import Field
from typing import Optional
from src.models.base_model import BaseModel


class ReviewRecord(BaseModel, table=True):
    __tablename__ = "review_records"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    repository_full_name: str = Field(index=True)
    pr_number: int = Field(index=True)
    reviewed_head_sha: str
    reviewed_base_sha: str
    status: str = Field(default="completed")
