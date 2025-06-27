import os
from sqlmodel import create_engine, Session
from src.utils.logger import logger
from src.config.settings import STATELESS_MODE

engine = None


def get_engine():
    global engine
    if STATELESS_MODE:
        logger.info(
            "Application is in STATELESS_MODE. Database engine will not be created."
        )
        return None

    if engine is None:
        logger.info("Database engine is not initialized. Creating a new one.")
        DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sourceant.db")
        connect_args = {}
        if DATABASE_URL.startswith("sqlite"):
            logger.info("Using SQLite database.")
            # This prevents 'ProgrammingError: SQLite objects created in a thread can only be used in that same thread'
            connect_args["check_same_thread"] = False
        else:
            logger.info("Using a non-SQLite database (e.g., PostgreSQL).")

        engine = create_engine(DATABASE_URL, echo=True, connect_args=connect_args)
        logger.info("Database engine created successfully.")
    return engine


def get_session():
    db_engine = get_engine()
    if db_engine is None:
        logger.error("Attempted to get a database session while in STATELESS_MODE.")
        raise RuntimeError("Application is in STATELESS_MODE. Cannot get a session.")

    with Session(db_engine) as session:
        yield session
