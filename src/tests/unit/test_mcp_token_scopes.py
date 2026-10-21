import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.settings import AuthSettings
from sqlalchemy import create_engine
from starlette.applications import Starlette
from starlette.routing import Mount

from src.api.routes import mcp_tokens
from src.core.context import DefaultContextProvider
from src.core.knowledge import KnowledgeObject, SQLKnowledgeRepository
from src.core.mcp import create_mcp_server, hosted_surface
from src.core.mcp.auth import EntitledScopeResolver, SourceAntTokenVerifier
from src.core.mcp.revocations import is_revoked, metadata
from src.core.scope import Scope

SECRET = "test-secret-value-with-at-least-32-bytes"
ORIGIN = "https://sourceant.example.com"
RESOURCE = f"{ORIGIN}/mcp"
SCOPE = {"repository": "acme/shop"}


def token(scopes: str, token_id: str = "one") -> str:
    return jwt.encode(
        {
            "sub": "user:1",
            "workspace": "one",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iss": "https://issuer.example.com",
            "aud": "sourceant-mcp",
            "scope": scopes,
            "jti": token_id,
        },
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp.db'}")
    metadata.create_all(engine)
    knowledge = SQLKnowledgeRepository(engine, create_schema=True)
    knowledge.put(
        Scope.from_mapping({"provider": "github", "repository": "acme/shop"}),
        KnowledgeObject(
            id="signed-requests",
            kind="decision",
            status="approved",
            summary="Use signed requests",
        ),
    )
    server = create_mcp_server(
        DefaultContextProvider(knowledge=knowledge),
        knowledge=knowledge,
        surface=hosted_surface(
            scope_resolver=EntitledScopeResolver(
                lambda workspace, repository: (
                    "github"
                    if (workspace, repository) == ("one", "acme/shop")
                    else None
                )
            ),
            auth=AuthSettings(
                issuer_url="https://issuer.example.com",
                resource_server_url=RESOURCE,
                required_scopes=["sourceant"],
            ),
            token_verifier=SourceAntTokenVerifier(
                issuer="https://issuer.example.com",
                audience="sourceant-mcp",
                required_scopes=frozenset({"sourceant"}),
                revoked=lambda token_id: is_revoked(engine, token_id),
            ),
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = Starlette(
        routes=[Mount("/mcp", app=server.streamable_http_app())], lifespan=lifespan
    )

    async def call(bearer: str, tool: str, arguments: dict):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=ORIGIN,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Host": "sourceant.example.com",
                "Origin": ORIGIN,
            },
            follow_redirects=True,
        ) as client:
            async with streamable_http_client(
                f"{RESOURCE}/", http_client=client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    return await session.call_tool(tool, arguments)

    return app, call, engine


def knowledge_write():
    return {
        "scope": SCOPE,
        "id": "retry-budget",
        "kind": "decision",
        "status": "approved",
        "summary": "Retry three times",
    }


@pytest.mark.asyncio
async def test_a_read_token_reads_and_cannot_write(served):
    app, call, _ = served
    async with app.router.lifespan_context(app):
        read = await call(token("sourceant read"), "search_knowledge", {"scope": SCOPE})
        written = await call(
            token("sourceant read"), "put_knowledge", knowledge_write()
        )

    assert read.isError is False
    assert read.structuredContent["total"] == 1
    assert written.isError is True
    assert "write:knowledge" in written.content[0].text


@pytest.mark.asyncio
async def test_a_write_scope_covers_only_its_own_area(served):
    app, call, _ = served
    async with app.router.lifespan_context(app):
        written = await call(
            token("sourceant read write:knowledge"), "put_knowledge", knowledge_write()
        )
        elsewhere = await call(
            token("sourceant write:requirements"), "search_knowledge", {"scope": SCOPE}
        )

    assert written.isError is False
    assert elsewhere.isError is True
    assert "read" in elsewhere.content[0].text


@pytest.mark.asyncio
async def test_a_token_from_before_scopes_keeps_full_access(served):
    app, call, _ = served
    async with app.router.lifespan_context(app):
        written = await call(token("sourceant"), "put_knowledge", knowledge_write())

    assert written.isError is False


@pytest.mark.asyncio
async def test_a_revoked_token_is_refused_through_the_route_the_gateway_calls(
    served, monkeypatch
):
    app, call, engine = served
    api = FastAPI()
    api.include_router(mcp_tokens.router, prefix="/api/mcp")
    monkeypatch.setattr(mcp_tokens, "get_engine", lambda: engine)
    client = TestClient(api)

    def gateway(claims: dict) -> dict:
        signed = jwt.encode(
            {"sub": "service:gateway", "scope": claims, "exp": time.time() + 300},
            SECRET,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {signed}"}

    body = {
        "token_id": "withdrawn",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    }
    assert (
        client.post("/api/mcp/revocations", json=body, headers=gateway({})).status_code
        == 403
    )
    assert (
        client.post(
            "/api/mcp/revocations", json=body, headers=gateway({"mcp_revocation": True})
        ).status_code
        == 204
    )

    async with app.router.lifespan_context(app):
        kept = await call(
            token("sourceant read", "kept"), "search_knowledge", {"scope": SCOPE}
        )
        with pytest.raises(Exception):
            await call(
                token("sourceant read", "withdrawn"),
                "search_knowledge",
                {"scope": SCOPE},
            )

    assert kept.isError is False
