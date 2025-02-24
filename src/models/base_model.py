from sqlmodel import SQLModel, Field, select
from datetime import datetime
from typing import Any, Dict
from src.config.db import get_session


class BaseModel(SQLModel):
    id: int = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Override dict method to handle datetime fields
    def dict(self, *args, **kwargs) -> Dict[str, Any]:
        data = self.model_dump(*args, **kwargs)

        # Convert datetime fields to ISO format
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    def save(self):
        """Save or update an instance."""
        with next(get_session()) as session:
            session.add(self)
            session.commit()
            session.refresh(self)
        return self

    @classmethod
    def get(cls, model_id: int):
        """Fetch a single record by ID."""
        with next(get_session()) as session:
            result = session.exec(select(cls).where(cls.id == model_id)).first()
        return result

    @classmethod
    def get_all(cls):
        """Fetch all records."""
        with next(get_session()) as session:
            return session.exec(select(cls)).all()

    @classmethod
    def delete(cls, model_id: int):
        """Delete a record by ID."""
        with next(get_session()) as session:
            instance = session.exec(select(cls).where(cls.id == model_id)).first()
            if instance:
                session.delete(instance)
                session.commit()
        return instance

    @classmethod
    def create(cls, **data):
        """Create a new record."""
        instance = cls(**data)
        return instance.save()
