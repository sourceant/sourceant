from typing import Optional

from sqlmodel import Field

from src.models.base_model import BaseModel


class ModelUsageRecord(BaseModel, table=True):
    __tablename__ = "model_usage"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    model: str = Field(index=True)
    purpose: str = Field(default="", index=True)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    reported_total: int = Field(default=0)
    cost: Optional[float] = Field(default=None)
    workspace: Optional[str] = Field(default=None, index=True)
    repository: Optional[str] = Field(default=None, index=True)
    organization: Optional[str] = Field(default=None)
    for_user: Optional[str] = Field(default=None)
