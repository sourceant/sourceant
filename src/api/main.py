from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from src.api.routes import pr as pr_endpoints
from src.api.routes import app as app_endpoints
from src.api.routes import health as health_endpoints

from src.auth import require_jwt_secret
from src.llms.llm_factory import llm
from src.utils.logger import setup_logger, logger
from src.core.plugins import plugin_manager
from src.mcp_server.application import create_http_mcp_server

mcp_server = create_http_mcp_server()
mcp_http_app = mcp_server.streamable_http_app() if mcp_server else None


def _read_version() -> str:
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        return version_file.read_text().strip()
    except FileNotFoundError:
        return "0.0.0-dev"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        if mcp_server is not None:
            await stack.enter_async_context(mcp_server.session_manager.run())
        setup_logger()
        logger.info("Starting up...")

        require_jwt_secret()

        llm()

        try:
            plugins_dir = Path(__file__).parent.parent / "plugins"
            plugin_manager.add_plugin_directory(plugins_dir)
            await plugin_manager.load_all_plugins()
            await plugin_manager.initialize_plugins()
            await plugin_manager.start_plugins()
            logger.info("Plugin system initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing plugin system: {e}", exc_info=True)

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
    description=(
        "An indexed graph of your codebase and the decisions behind it, "
        "served over MCP"
    ),
    version=_read_version(),
    lifespan=lifespan,
)

from src.api.routes import code as code_endpoints
from src.api.routes import knowledge as knowledge_endpoints
from src.api.routes import repos as repo_endpoints
from src.api.routes import reviews as review_endpoints
from src.api.routes import settings as settings_endpoints
from src.api.routes import topology as topology_endpoints
from src.api.routes import triage as triage_endpoints

app.include_router(health_endpoints.router, tags=["health"])
app.include_router(app_endpoints.router, tags=["general"])
app.include_router(pr_endpoints.router, prefix="/api/prs", tags=["pull_requests"])
app.include_router(repo_endpoints.router, prefix="/api/repos", tags=["repositories"])
app.include_router(review_endpoints.router, prefix="/api/reviews", tags=["reviews"])
app.include_router(triage_endpoints.router, prefix="/api/triage", tags=["triage"])
app.include_router(settings_endpoints.router, prefix="/api/settings", tags=["settings"])
app.include_router(topology_endpoints.router, prefix="/api/topology", tags=["topology"])
app.include_router(code_endpoints.router, prefix="/api/code", tags=["code"])
app.include_router(
    knowledge_endpoints.router, prefix="/api/knowledge", tags=["knowledge"]
)
if mcp_http_app is not None:
    app.mount("/mcp", mcp_http_app)
