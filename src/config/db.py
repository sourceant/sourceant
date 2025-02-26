from sqlmodel import create_engine, Session
import os

engine = None


def get_engine():
    global engine
    if engine is None:
        DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sourceant.db")
        engine = create_engine(DATABASE_URL, echo=True)
    return engine


def get_session():
    with Session(get_engine()) as session:
        yield session
