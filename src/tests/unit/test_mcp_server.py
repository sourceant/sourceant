from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.settings import AuthSettings
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import create_engine
from starlette.applications import Starlette
from starlette.routing import Mount

from src.core.code_index import CodeNode, InMemoryCodeIndex
from src.core.context import DefaultContextProvider
from src.core.knowledge import (
    InMemoryKnowledgeRepository,
    Knowledge,
    SQLKnowledgeRepository,
)
from src.core.scope import Scope
from src.mcp_server import create_mcp_server
from src.mcp_server.application import create_http_mcp_server
from src.mcp_server.auth import PrincipalScopeResolver, SourceAntTokenVerifier

PROJECT = Scope.from_mapping({"project": "one"})
OTHER_PROJECT = Scope.from_mapping({"project": "two"})


def test_http_mcp_is_disabled_without_authorization_settings(monkeypatch):
    for name in (
        "MCP_HTTP_ISSUER_URL",
        "MCP_HTTP_RESOURCE_URL",
        "MCP_HTTP_AUDIENCE",
        "JWT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    assert create_http_mcp_server() is None


def test_http_mcp_rejects_partial_authorization_settings(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_ISSUER_URL", "https://issuer.example.com")
    monkeypatch.delenv("MCP_HTTP_RESOURCE_URL", raising=False)
    monkeypatch.delenv("MCP_HTTP_AUDIENCE", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValueError, match="resource, audience, JWT_SECRET"):
        create_http_mcp_server()


@pytest.mark.asyncio
async def test_mcp_get_context_uses_protocol_boundary_and_isolates_scope():
    code = InMemoryCodeIndex()
    knowledge = InMemoryKnowledgeRepository()
    for scope, summary in ((PROJECT, "Use one"), (OTHER_PROJECT, "Use two")):
        code.put_node(
            scope,
            CodeNode("handler", frozenset({"Function"}), {"scope": summary}),
        )
        knowledge.put(scope, Knowledge("rule", "rule", "approved", summary))

    server = create_mcp_server(DefaultContextProvider(code=code, knowledge=knowledge))
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        result = await session.call_tool(
            "get_context",
            {
                "scope": {"project": "one"},
                "code_node_ids": ["handler"],
                "knowledge_ids": ["rule"],
            },
        )

    assert {tool.name for tool in tools.tools} == {
        "get_context",
        "put_knowledge",
        "put_knowledge_relationship",
        "search_knowledge",
    }
    assert result.isError is False
    assert result.structuredContent["scope"] == {"project": "one"}
    assert result.structuredContent["code"]["nodes"][0]["properties"] == {
        "scope": "Use one"
    }
    assert result.structuredContent["knowledge"]["items"][0]["summary"] == "Use one"
    assert result.structuredContent["truncated"] is False


@pytest.mark.asyncio
async def test_mcp_get_context_rejects_unbounded_and_empty_requests():
    server = create_mcp_server(DefaultContextProvider(code=InMemoryCodeIndex()))

    async with create_connected_server_and_client_session(server) as session:
        excessive = await session.call_tool(
            "get_context",
            {
                "scope": {"project": "one"},
                "code_node_ids": ["handler"],
                "depth": 4,
            },
        )
        excessive_limit = await session.call_tool(
            "get_context",
            {
                "scope": {"project": "one"},
                "code_node_ids": ["handler"],
                "limit": 51,
            },
        )
        empty = await session.call_tool(
            "get_context",
            {"scope": {"project": "one"}},
        )

    assert excessive.isError is True
    assert excessive_limit.isError is True
    assert empty.isError is True


@pytest.mark.asyncio
async def test_mcp_manages_durable_knowledge_through_protocol_boundary(tmp_path):
    knowledge = SQLKnowledgeRepository(
        create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}"),
        create_schema=True,
    )
    server = create_mcp_server(
        DefaultContextProvider(knowledge=knowledge),
        knowledge=knowledge,
    )

    async with create_connected_server_and_client_session(server) as session:
        for identifier, summary in (
            ("decision", "Use signed requests"),
            ("constraint", "Reject unsigned requests"),
        ):
            result = await session.call_tool(
                "put_knowledge",
                {
                    "scope": {"project": "one"},
                    "id": identifier,
                    "kind": identifier,
                    "status": "approved",
                    "summary": summary,
                },
            )
            assert result.isError is False
        relationship = await session.call_tool(
            "put_knowledge_relationship",
            {
                "scope": {"project": "one"},
                "id": "decision-constraint",
                "source_id": "decision",
                "target_id": "constraint",
                "type": "depends_on",
                "status": "approved",
            },
        )
        search = await session.call_tool(
            "search_knowledge",
            {
                "scope": {"project": "one"},
                "statuses": ["approved"],
            },
        )
        other_scope = await session.call_tool(
            "search_knowledge",
            {"scope": {"project": "two"}},
        )
        context = await session.call_tool(
            "get_context",
            {
                "scope": {"project": "one"},
                "knowledge_ids": ["decision"],
            },
        )

    assert relationship.isError is False
    assert search.structuredContent["total"] == 2
    assert other_scope.structuredContent["total"] == 0
    assert [item["id"] for item in context.structuredContent["knowledge"]["items"]] == [
        "decision",
        "constraint",
    ]


@pytest.mark.asyncio
async def test_streamable_http_authenticates_and_isolates_principals(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-with-at-least-32-bytes")
    knowledge = SQLKnowledgeRepository(
        create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}"),
        create_schema=True,
    )
    server = create_mcp_server(
        DefaultContextProvider(knowledge=knowledge),
        knowledge=knowledge,
        scope_resolver=PrincipalScopeResolver(),
        auth=AuthSettings(
            issuer_url="https://issuer.example.com",
            resource_server_url="https://sourceant.example.com/mcp",
            required_scopes=["sourceant"],
        ),
        token_verifier=SourceAntTokenVerifier(
            issuer="https://issuer.example.com",
            audience="sourceant-mcp",
            required_scopes=frozenset({"sourceant"}),
        ),
    )
    mcp_app = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = Starlette(routes=[Mount("/mcp", app=mcp_app)], lifespan=lifespan)

    async def use_client(subject, action):
        token = jwt.encode(
            {
                "sub": subject,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                "iss": "https://issuer.example.com",
                "aud": "sourceant-mcp",
                "scope": "sourceant",
            },
            "test-secret-value-with-at-least-32-bytes",
            algorithm="HS256",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:8000",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client(
                "http://localhost:8000/mcp/", http_client=client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    return await action(session)

    async def put(session):
        return await session.call_tool(
            "put_knowledge",
            {
                "scope": {"repository": "shop"},
                "id": "decision",
                "kind": "decision",
                "status": "approved",
                "summary": "Use signed requests",
            },
        )

    async def search(session):
        return await session.call_tool(
            "search_knowledge",
            {"scope": {"repository": "shop"}},
        )

    async with app.router.lifespan_context(app):
        stored = await use_client("one", put)
        owner_result = await use_client("one", search)
        other_result = await use_client("two", search)

    assert stored.isError is False
    assert owner_result.structuredContent["total"] == 1
    assert other_result.structuredContent["total"] == 0
