from __future__ import annotations

import os

from mcp.server.auth.settings import AuthSettings

from src.config.db import get_engine
from src.core.code_index import InMemoryCodeIndex
from src.core.context import DefaultContextProvider
from src.core.contracts import InMemoryContractRepository
from src.core.knowledge import (
    InMemoryKnowledgeRepository,
    SQLKnowledgeRepository,
)
from src.core.review_state import InMemoryReviewStateRepository
from src.core.topology import InMemoryTopologyRepository

from .auth import PrincipalScopeResolver, SourceAntTokenVerifier
from .server import create_mcp_server


def create_default_mcp_server():
    engine = get_engine()
    knowledge = (
        SQLKnowledgeRepository(engine)
        if engine is not None
        else InMemoryKnowledgeRepository()
    )
    provider = DefaultContextProvider(
        code=InMemoryCodeIndex(),
        knowledge=knowledge,
        topology=InMemoryTopologyRepository(),
        contracts=InMemoryContractRepository(),
        review_state=InMemoryReviewStateRepository(),
    )
    return create_mcp_server(
        provider,
        knowledge=knowledge,
    )


def create_http_mcp_server():
    values = {
        "issuer": os.getenv("MCP_HTTP_ISSUER_URL"),
        "resource": os.getenv("MCP_HTTP_RESOURCE_URL"),
        "audience": os.getenv("MCP_HTTP_AUDIENCE"),
    }
    if not any(values.values()):
        return None
    missing = [key for key, value in values.items() if not value]
    if not os.getenv("JWT_SECRET"):
        missing.append("JWT_SECRET")
    if missing:
        raise ValueError(
            f"incomplete MCP HTTP authorization settings: {', '.join(missing)}"
        )
    required_scopes = frozenset(
        item
        for item in os.getenv("MCP_HTTP_REQUIRED_SCOPES", "sourceant").split()
        if item
    )
    engine = get_engine()
    knowledge = (
        SQLKnowledgeRepository(engine)
        if engine is not None
        else InMemoryKnowledgeRepository()
    )
    provider = DefaultContextProvider(
        code=InMemoryCodeIndex(),
        knowledge=knowledge,
        topology=InMemoryTopologyRepository(),
        contracts=InMemoryContractRepository(),
        review_state=InMemoryReviewStateRepository(),
    )
    return create_mcp_server(
        provider,
        knowledge=knowledge,
        scope_resolver=PrincipalScopeResolver(),
        auth=AuthSettings(
            issuer_url=values["issuer"],
            resource_server_url=values["resource"],
            required_scopes=sorted(required_scopes),
        ),
        token_verifier=SourceAntTokenVerifier(
            issuer=values["issuer"],
            audience=values["audience"],
            required_scopes=required_scopes,
        ),
    )
