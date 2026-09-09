import os
import tempfile
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine
import uvicorn

from src.api.routes import artifacts, requirements, topology, usage
from src.core.knowledge import SQLKnowledgeRepository
from src.core.requirements import KnowledgeBackedRequirements, SQLRequirementsRepository
from src.core.storage import FileSystemArtifactStore
from src.core.topology import SQLTopologyRepository
from src.core.usage.reading import SQLUsageReader
from src.models.token_usage import TokenUsageRecord


def application(root):
    engine = create_engine(f"sqlite:///{root / 'backend.db'}")
    knowledge = SQLKnowledgeRepository(engine, create_schema=True)
    requirements_store = KnowledgeBackedRequirements(
        SQLRequirementsRepository(engine, create_schema=True), knowledge
    )
    graph = SQLTopologyRepository(engine, create_schema=True)
    TokenUsageRecord.__table__.create(engine, checkfirst=True)
    app = FastAPI()
    for name, module in (
        ("requirements", requirements),
        ("artifacts", artifacts),
        ("topology", topology),
        ("usage", usage),
    ):
        app.include_router(module.router, prefix=f"/api/{name}")
    app.dependency_overrides[requirements.get_requirements] = lambda: requirements_store
    app.dependency_overrides[topology.get_topology_repository] = lambda: graph
    store = FileSystemArtifactStore(root / "artifacts")
    app.dependency_overrides[artifacts.get_artifacts] = lambda: store
    artifacts.get_artifacts = lambda: store
    app.dependency_overrides[usage.get_usage_reader] = lambda: SQLUsageReader(engine)
    requirements.connected_names = lambda user: []
    return app


if __name__ == "__main__":
    if not os.environ.get("JWT_SECRET"):
        raise RuntimeError("JWT_SECRET is required")
    with tempfile.TemporaryDirectory(prefix="dashboard-backend-") as directory:
        uvicorn.run(
            application(Path(directory)), host="0.0.0.0", port=9901, log_level="warning"
        )
