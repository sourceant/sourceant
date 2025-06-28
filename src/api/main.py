from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from src.api.routes import pr as pr_endpoints
from src.api.routes import app as app_endpoints
from src.api.handlers.exception_handlers import unprocessable_entity_exception_handler


from src.llms.llm_factory import llm
from src.utils.logger import setup_logger

setup_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    This context manager handles application startup and shutdown events.
    On startup, it primes the LLM singleton cache and sets up the database.
    """
    print("Starting up...")
    # Prime the LLM singleton cache on startup in the main thread.
    # This ensures the client is created with a running asyncio event loop.
    # Subsequent calls to llm() from background tasks will hit the cache
    # and receive the already-initialized instance.
    llm()

    yield

    print("Shutting down...")


app = FastAPI(
    title="🐜 SourceAnt 🐜",
    description="Automated code review tool",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_exception_handler(RequestValidationError, unprocessable_entity_exception_handler)

app.include_router(app_endpoints.router, tags=["general"])
app.include_router(pr_endpoints.router, prefix="/api/prs", tags=["pull_requests"])
