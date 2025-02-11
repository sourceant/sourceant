from sqlmodel import SQLModel, Field, Session, create_engine, select
from datetime import datetime
import os
from dotenv import load_dotenv
from typing import Any, Dict

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:admin@db:5432/sourceant")

engine = create_engine(DATABASE_URL)


class BaseModel(SQLModel):
    id: int = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # Override dict method to handle datetime fields
    def dict(self, *args, **kwargs) -> Dict[str, Any]:
        data = super().model_dump(*args, **kwargs)

        # Convert datetime fields to ISO format
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def get_session(cls) -> Session:
        return Session(engine)

    def save(self):
        with self.get_session() as session:
            session.add(self)
            session.commit()
            session.refresh(self)
        return self

    @classmethod
    def get(cls, model_id: int):
        with cls.get_session() as session:
            return session.exec(select(cls).where(cls.id == model_id)).first()

    @classmethod
    def get_all(cls):
        with cls.get_session() as session:
            return session.exec(select(cls)).all()

    @classmethod
    def delete(cls, model_id: int):
        with cls.get_session() as session:
            instance = session.exec(select(cls).where(cls.id == model_id)).first()
            if instance:
                session.delete(instance)
                session.commit()
        return instance

    @classmethod
    def create(cls, **data):
        instance = cls(**data)
        return instance.save()
