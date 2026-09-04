from typing import Optional

from sqlalchemy import BigInteger
from sqlmodel import Field

from src.models.base_model import BaseModel


class ModelUsageRecord(BaseModel, table=True):
    __tablename__ = "model_usage"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(default="", index=True)
    model: str = Field(index=True)
    purpose: str = Field(default="", index=True)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    reported_total: int = Field(default=0)
    cost_micro: Optional[int] = Field(default=None, sa_type=BigInteger)
    currency: str = Field(default="USD")
    owner_type: Optional[str] = Field(default=None)
    owner_id: Optional[str] = Field(default=None)
    subject_type: Optional[str] = Field(default=None)
    subject_id: Optional[str] = Field(default=None)
