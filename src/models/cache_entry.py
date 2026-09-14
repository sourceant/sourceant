from datetime import datetime

from sqlalchemy import Text, UniqueConstraint
from sqlmodel import Field

from src.models.base_model import BaseModel


class CacheEntry(BaseModel, table=True):
    __tablename__ = "cache_entries"
    # Declared here as well as in the migration: a machine that runs no
    # migrations builds this table from the model, and without the constraint
    # two writers racing on one key both insert instead of one taking the
    # other's row.
    __table_args__ = (
        UniqueConstraint("namespace", "key", name="uq_cache_entries_namespace_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    # What the entry is for. Indexed so a namespace can be read or cleared on
    # its own, and so the table can be understood by someone looking at it.
    namespace: str = Field(max_length=64, index=True)
    key: str = Field(max_length=255, index=True)
    value: str = Field(sa_type=Text)
    expires_at: datetime = Field(index=True)
