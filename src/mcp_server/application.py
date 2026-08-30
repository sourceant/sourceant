"""Assembling the MCP server this deployment serves.

One assembly. Which surface it gets is decided by whether an issuer is named:
an issuer means hosted and authenticated, no issuer means personal and
loopback-only, and a half-configured deployment serves nothing rather than an
open server.
"""

from __future__ import annotations

import os
from typing import Optional

from mcp.server.auth.settings import AuthSettings

from src.config.db import get_engine
from src.core.code_index import (
    CodeIndexReader,
    InMemoryCodeIndex,
    ResolvingCodeIndexReader,
    SQLCodeIndexRepository,
)
from src.core.context import DefaultContextProvider
from src.core.contracts import InMemoryContractRepository
from src.core.knowledge import (
    InMemoryKnowledgeRepository,
    SQLKnowledgeRepository,
)
from src.core.mcp import Surface, create_mcp_server, hosted_surface, personal_surface
from src.core.mcp.auth import (
    EntitledScopeResolver,
    SourceAntTokenVerifier,
    connected_repository_entitlement,
)
from src.core.requirements import (
    KnowledgeBackedRequirements,
    SQLRequirementsRepository,
)
from src.core.review import InMemoryFindingStore, finding_store
from src.core.services import service_registry
from src.core.topology import InMemoryTopologyRepository, SQLTopologyRepository
from src.utils.logger import logger


def create_default_mcp_server():
    """The stdio server.

    Stdio has no listening socket, so the transport itself is the evidence that
    the client and the checkout are on one computer.
    """
    return _assemble(personal_surface())


def create_http_mcp_server():
    """The HTTP server, or None where this deployment serves none."""
    surface = mcp_surface()
    if surface is None:
        return None
    # Before assembling: a server advertises the tools that exist when it is
    # built, and the plugins contributing them are otherwise initialized later
    # in the application's lifespan.
    load_plugins()
    return _assemble(surface)


def load_plugins() -> None:
    """Initialize plugins, so the tools they contribute are advertised.

    Only as far as initializing. Starting them subscribes to webhook events,
    which the application's own lifespan does when it is serving them.
    """
    import asyncio
    from pathlib import Path

    from src.core.plugins import plugin_manager

    async def load() -> None:
        plugin_manager.add_plugin_directory(Path(__file__).parent.parent / "plugins")
        await plugin_manager.load_all_plugins()
        await plugin_manager.initialize_plugins()

    try:
        asyncio.run(load())
    except Exception as error:  # noqa: BLE001 - serve core's own tools regardless
        logger.warning(f"MCP tools from plugins are unavailable: {error}")


def mcp_surface() -> Optional[Surface]:
    """Which surface this deployment's HTTP endpoint serves, if any."""
    issuer = os.getenv("MCP_HTTP_ISSUER_URL")
    if issuer:
        return _hosted(issuer)
    if _local_mode():
        return personal_surface()
    return None


def _hosted(issuer: str) -> Surface:
    values = {
        "issuer": issuer,
        "resource": os.getenv("MCP_HTTP_RESOURCE_URL"),
        "audience": os.getenv("MCP_HTTP_AUDIENCE"),
    }
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
    return hosted_surface(
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
        scope_resolver=EntitledScopeResolver(
            connected_repository_entitlement(get_engine())
        ),
    )


def _local_mode() -> bool:
    # Read through the module: serve_command sets the attribute after settings
    # is imported.
    from src.config import settings

    return bool(getattr(settings, "LOCAL_MODE", False))


def _assemble(surface: Surface):
    knowledge, topology, code, requirements = _repositories(get_engine())
    provider = DefaultContextProvider(
        code=code,
        knowledge=knowledge,
        topology=topology,
        contracts=InMemoryContractRepository(),
        # Whatever keeps findings, or one that forgets when nothing does.
        review_state=finding_store() or InMemoryFindingStore(),
        requirements=requirements,
    )
    return create_mcp_server(
        provider,
        code=code,
        knowledge=knowledge,
        topology=topology,
        requirements=requirements,
        surface=surface,
    )


def _repositories(engine):
    knowledge = (
        SQLKnowledgeRepository(engine)
        if engine is not None
        else InMemoryKnowledgeRepository()
    )
    topology = (
        SQLTopologyRepository(engine)
        if engine is not None
        else InMemoryTopologyRepository()
    )
    return (
        knowledge,
        topology,
        _resolving_code_index(engine),
        _requirements(engine, knowledge),
    )


def _requirements(engine, knowledge):
    if engine is None:
        return None
    return KnowledgeBackedRequirements(SQLRequirementsRepository(engine), knowledge)


def _resolving_code_index(engine) -> CodeIndexReader:
    return ResolvingCodeIndexReader(
        lambda: service_registry.resolve(CodeIndexReader),
        (SQLCodeIndexRepository(engine) if engine is not None else InMemoryCodeIndex()),
    )
