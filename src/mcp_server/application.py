from __future__ import annotations

import os
from threading import Thread

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
from src.core.requirements import (
    KnowledgeBackedRequirements,
    SQLRequirementsRepository,
)
from src.core.review_state import InMemoryReviewStateRepository
from src.core.services import service_registry
from src.core.topology import InMemoryTopologyRepository, SQLTopologyRepository

from .auth import (
    EntitledScopeResolver,
    SourceAntTokenVerifier,
    connected_repository_entitlement,
)
from .server import create_mcp_server


def create_default_mcp_server():
    engine = get_engine()
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
    code = _resolving_code_index(engine)
    requirements = _requirements(engine, knowledge)
    provider = DefaultContextProvider(
        code=code,
        knowledge=knowledge,
        topology=topology,
        contracts=InMemoryContractRepository(),
        review_state=InMemoryReviewStateRepository(),
        requirements=requirements,
    )
    return create_mcp_server(
        provider,
        code=code,
        knowledge=knowledge,
        topology=topology,
        requirements=requirements,
        reviews=_reviewer(),
    )


def _reviewer():
    """How this server starts a review, or None where it cannot.

    Only the local surface reviews a checkout: it reads files off a disk, and a
    hosted deployment has nobody's disk to read. The routes own the work, so
    this asks them rather than growing a second copy of it.
    """
    from src.config.settings import LOCAL_MODE

    if not LOCAL_MODE:
        return None

    def start(repository: str, title: str = "") -> dict:
        from src.api.routes.local_reviews import (
            ReviewInput,
            get_knowledge,
            get_reviews,
            kept,
            run,
        )
        from src.core.local_reviews import LocalReview, named
        from src.api.routes.code import find_repository

        find_repository(repository)
        body = ReviewInput(repository=repository, title=title)
        store, reviews = get_knowledge(), get_reviews()

        identifier = named()
        started = reviews.put(
            LocalReview(id=identifier, repository=repository, title=title)
        )
        # Not a background task: nothing here is inside a request, so the work
        # goes on a thread of its own and this answers immediately.
        Thread(
            target=run,
            args=(identifier, body, store, reviews),
            daemon=True,
        ).start()

        answer = kept(started)
        answer["url"] = _where(answer["path"])
        return answer

    return start


def _where(path: str) -> str:
    """A link somebody can click, where this machine knows its own address.

    The agent serves the screen and knows where it is listening; core is told
    on the way in. Without that, the path is the honest answer.
    """
    base = os.getenv("SOURCEANT_UI_URL", "").rstrip("/")
    return f"{base}/{path}" if base else path


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
    topology = (
        SQLTopologyRepository(engine)
        if engine is not None
        else InMemoryTopologyRepository()
    )
    code = _resolving_code_index(engine)
    requirements = _requirements(engine, knowledge)
    provider = DefaultContextProvider(
        code=code,
        knowledge=knowledge,
        topology=topology,
        contracts=InMemoryContractRepository(),
        review_state=InMemoryReviewStateRepository(),
        requirements=requirements,
    )
    return create_mcp_server(
        provider,
        code=code,
        knowledge=knowledge,
        topology=topology,
        requirements=requirements,
        scope_resolver=EntitledScopeResolver(connected_repository_entitlement(engine)),
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


def _requirements(engine, knowledge):
    if engine is None:
        return None
    return KnowledgeBackedRequirements(SQLRequirementsRepository(engine), knowledge)


def _resolving_code_index(engine) -> CodeIndexReader:
    return ResolvingCodeIndexReader(
        lambda: service_registry.resolve(CodeIndexReader),
        (SQLCodeIndexRepository(engine) if engine is not None else InMemoryCodeIndex()),
    )
