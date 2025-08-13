from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from src.api.routes import pr as pr_endpoints
from src.api.routes import app as app_endpoints

from src.llms.llm_factory import llm
from src.utils.logger import setup_logger
from src.plugins.plugin_manager import plugin_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    This context manager handles application startup and shutdown events.
    On startup, it primes the LLM singleton cache and sets up the database,
    and initializes the plugin system.
    """
    print("Starting up...")
    setup_logger()
    
    # Prime the LLM singleton cache on startup in the main thread.
    # This ensures the client is created with a running asyncio event loop.
    # Subsequent calls to llm() from background tasks will hit the cache
    # and receive the already-initialized instance.
    llm()
    
    # Initialize plugin system
    try:
        # Add plugins directory (includes builtin and external plugins)
        plugins_dir = Path(__file__).parent.parent / "plugins"
        plugin_manager.add_plugin_directory(plugins_dir)
        
        # Load and initialize plugins
        await plugin_manager.load_all_plugins()
        await plugin_manager.initialize_plugins()
        await plugin_manager.start_plugins()
        
        print("Plugin system initialized successfully")
    except Exception as e:
        print(f"Error initializing plugin system: {e}")
        # Continue startup even if plugins fail to load
    
    yield
    
    print("Shutting down...")
    
    # Shutdown plugin system
    try:
        await plugin_manager.stop_plugins()
        await plugin_manager.cleanup_plugins()
        print("Plugin system shutdown completed")
    except Exception as e:
        print(f"Error shutting down plugin system: {e}")


app = FastAPI(
    title="🐜 SourceAnt 🐜",
    description="Automated code review tool",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(app_endpoints.router, tags=["general"])
app.include_router(pr_endpoints.router, prefix="/api/prs", tags=["pull_requests"])
