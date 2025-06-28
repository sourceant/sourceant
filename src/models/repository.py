from sqlmodel import Field
from typing import Optional
from src.models.base_model import BaseModel, get_session, select


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
    created_at: str
    updated_at: str

    def __repr__(self):
        return f"<Repository(provider={self.provider}, name={self.name}, full_name={self.full_name})>"

    @classmethod
    def get_by_full_name(cls, full_name: str):
        """Fetch a single record by full_name."""
        with next(get_session()) as session:
            result = session.exec(
                select(cls).where(cls.full_name == full_name)
            ).first()
        return result
