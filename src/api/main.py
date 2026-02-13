from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from src.api.routes import pr as pr_endpoints
from src.api.routes import app as app_endpoints

from src.llms.llm_factory import llm
from src.utils.logger import setup_logger, logger
from src.core.plugins import plugin_manager


def _read_version() -> str:
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        return version_file.read_text().strip()
    except FileNotFoundError:
        return "0.0.0-dev"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    This context manager handles application startup and shutdown events.
    On startup, it primes the LLM singleton cache and sets up the database,
    and initializes the plugin system.
    """
    setup_logger()
    logger.info("Starting up...")

    # Prime the LLM singleton cache on startup in the main thread.
    # This ensures the client is created with a running asyncio event loop.
    # Subsequent calls to llm() from background tasks will hit the cache
    # and receive the already-initialized instance.
    llm()

    # Initialize plugin system
    try:
        # Add plugins directory
        plugins_dir = Path(__file__).parent.parent / "plugins"
        plugin_manager.add_plugin_directory(plugins_dir)

        # Load and initialize plugins
        await plugin_manager.load_all_plugins()
        await plugin_manager.initialize_plugins()
        await plugin_manager.start_plugins()

        logger.info("Plugin system initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing plugin system: {e}", exc_info=True)
        # Continue startup even if plugins fail to load

    yield

    logger.info("Shutting down...")

    try:
        await plugin_manager.stop_plugins()
        await plugin_manager.cleanup_plugins()
        logger.info("Plugin system shutdown completed")
    except Exception as e:
        logger.error(f"Error shutting down plugin system: {e}", exc_info=True)


app = FastAPI(
    title="🐜 SourceAnt 🐜",
    description="Automated code review tool",
    version=_read_version(),
    lifespan=lifespan,
)

from src.api.routes import repos as repo_endpoints

app.include_router(app_endpoints.router, tags=["general"])
app.include_router(pr_endpoints.router, prefix="/api/prs", tags=["pull_requests"])
app.include_router(repo_endpoints.router, prefix="/api/repos", tags=["repositories"])
