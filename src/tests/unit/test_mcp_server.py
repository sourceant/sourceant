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

from src.core.code_index import (
    CodeEdge,
    CodeNode,
    InMemoryCodeIndex,
    ResolvingCodeIndexReader,
)
from src.core.context import DefaultContextProvider
from src.core.knowledge import (
    InMemoryKnowledgeRepository,
    KnowledgeObject,
    SQLKnowledgeRepository,
)
from src.core.scope import Scope
from src.core.topology import SQLTopologyRepository
from src.mcp_server import create_mcp_server
from src.mcp_server.application import create_http_mcp_server
from src.mcp_server.auth import EntitledScopeResolver, SourceAntTokenVerifier

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
        knowledge.put(scope, KnowledgeObject("rule", "rule", "approved", summary))

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
        "search_code",
        "trace_code",
        "get_context",
        "put_knowledge",
        "put_knowledge_relationship",
        "search_knowledge",
        "put_topology_entity",
        "put_topology_relationship",
        "traverse_topology",
        "put_requirement",
        "link_requirement",
        "search_requirements",
        "get_requirement_coverage",
        "link_knowledge",
    }
    assert result.isError is False
    assert result.structuredContent["scope"] == {"project": "one"}
    assert result.structuredContent["code"]["nodes"][0]["properties"] == {
        "scope": "Use one"
    }
    assert result.structuredContent["knowledge"]["items"][0]["summary"] == "Use one"
    assert result.structuredContent["truncated"] is False


@pytest.mark.asyncio
async def test_mcp_discovers_code_from_a_reader_registered_after_server_creation():
    fallback = InMemoryCodeIndex()
    active = None

    def resolve():
        if active is None:
            raise LookupError("not registered")
        return active

    code = ResolvingCodeIndexReader(resolve, fallback)
    server = create_mcp_server(DefaultContextProvider(code=code), code=code)

    supplied = InMemoryCodeIndex()
    supplied.put_node(
        PROJECT,
        CodeNode("file:api.py", frozenset({"File"}), {"file_path": "api.py"}),
    )
    supplied.put_node(
        PROJECT,
        CodeNode(
            "function:handle",
            frozenset({"Function"}),
            {"name": "handle", "file_path": "api.py"},
        ),
    )
    supplied.put_edge(
        PROJECT,
        CodeEdge("defines", "file:api.py", "function:handle", "DEFINES"),
    )
    active = supplied

    async with create_connected_server_and_client_session(server) as session:
        search = await session.call_tool(
            "search_code",
            {
                "scope": {"project": "one"},
                "labels": ["Function"],
                "properties": {"name": "handle"},
            },
        )
        trace = await session.call_tool(
            "trace_code",
            {
                "scope": {"project": "one"},
                "node_ids": ["function:handle"],
                "edge_types": ["DEFINES"],
            },
        )

    assert search.isError is False
    assert [node["id"] for node in search.structuredContent["nodes"]] == [
        "function:handle"
    ]
    assert {node["id"] for node in trace.structuredContent["nodes"]} == {
        "file:api.py",
        "function:handle",
    }


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
async def test_mcp_manages_durable_topology_through_protocol_boundary(tmp_path):
    topology = SQLTopologyRepository(
        create_engine(f"sqlite:///{tmp_path / 'topology.db'}"),
        create_schema=True,
    )
    server = create_mcp_server(
        DefaultContextProvider(topology=topology),
        topology=topology,
    )

    async with create_connected_server_and_client_session(server) as session:
        for identifier in ("checkout", "ledger"):
            entity = await session.call_tool(
                "put_topology_entity",
                {
                    "scope": {"workspace": "one"},
                    "id": identifier,
                    "kind": "service",
                    "status": "approved",
                },
            )
            assert entity.isError is False
        relationship = await session.call_tool(
            "put_topology_relationship",
            {
                "scope": {"workspace": "one"},
                "id": "checkout-ledger",
                "source_id": "checkout",
                "target_id": "ledger",
                "type": "depends_on",
                "status": "pending",
                "confidence": 0.6,
                "evidence": [{"id": "commit-1", "kind": "commit", "source": "github"}],
            },
        )
        traversal = await session.call_tool(
            "traverse_topology",
            {"scope": {"workspace": "one"}, "entity_ids": ["checkout"]},
        )
        approved_only = await session.call_tool(
            "traverse_topology",
            {
                "scope": {"workspace": "one"},
                "entity_ids": ["checkout"],
                "relationship_statuses": ["approved"],
            },
        )
        other_scope = await session.call_tool(
            "traverse_topology",
            {"scope": {"workspace": "two"}, "entity_ids": ["checkout"]},
        )
        context = await session.call_tool(
            "get_context",
            {
                "scope": {"workspace": "one"},
                "topology_entity_ids": ["checkout"],
            },
        )

    assert relationship.isError is False
    assert [edge["id"] for edge in traversal.structuredContent["relationships"]] == [
        "checkout-ledger"
    ]
    assert traversal.structuredContent["relationships"][0]["evidence"][0]["id"] == (
        "commit-1"
    )
    assert approved_only.structuredContent["relationships"] == []
    assert other_scope.structuredContent["entities"] == []
    assert [
        entity["id"] for entity in context.structuredContent["topology"]["entities"]
    ] == ["checkout", "ledger"]


@pytest.mark.asyncio
async def test_mcp_topology_tools_are_unavailable_without_a_repository():
    server = create_mcp_server(DefaultContextProvider(code=InMemoryCodeIndex()))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "put_topology_entity",
            {
                "scope": {"workspace": "one"},
                "id": "checkout",
                "kind": "service",
                "status": "approved",
            },
        )

    assert result.isError is True
    assert "topology management is not configured" in result.content[0].text


@pytest.mark.asyncio
async def test_streamable_http_serves_what_the_caller_is_entitled_to(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-with-at-least-32-bytes")
    knowledge = SQLKnowledgeRepository(
        create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}"),
        create_schema=True,
    )

    # Written the way a review or an initialization writes it: under the
    # repository, with no idea who will read it back.
    knowledge.put(
        Scope.from_mapping({"provider": "github", "repository": "acme/shop"}),
        KnowledgeObject(
            id="signed-requests",
            kind="decision",
            status="approved",
            summary="Use signed requests",
        ),
    )

    entitlements = {("one", "acme/shop"): "github"}
    server = create_mcp_server(
        DefaultContextProvider(knowledge=knowledge),
        knowledge=knowledge,
        scope_resolver=EntitledScopeResolver(
            lambda workspace, repository: entitlements.get((workspace, repository))
        ),
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

    async def use_client(workspace, action):
        token = jwt.encode(
            {
                "sub": f"user:1",
                # The workspace is a claim, never something sent with a request.
                "workspace": workspace,
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

    async def search(session):
        return await session.call_tool(
            "search_knowledge",
            {"scope": {"repository": "acme/shop"}},
        )

    async def search_elsewhere(session):
        return await session.call_tool(
            "search_knowledge",
            {"scope": {"repository": "someone/else"}},
        )

    async def search_without_a_repository(session):
        return await session.call_tool(
            "search_knowledge",
            {"scope": {"workspace": "acme"}},
        )

    async with app.router.lifespan_context(app):
        entitled = await use_client("one", search)
        elsewhere = await use_client("one", search_elsewhere)
        unscoped = await use_client("one", search_without_a_repository)
        stranger = await use_client("two", search)

    # The whole point: knowledge captured by SourceAnt is readable over MCP.
    assert entitled.structuredContent["total"] == 1

    assert elsewhere.isError is True
    assert "not entitled to someone/else" in elsewhere.content[0].text

    assert unscoped.isError is True
    assert "scope must name a repository" in unscoped.content[0].text

    assert stranger.isError is True
    assert "not entitled to acme/shop" in stranger.content[0].text


@pytest.mark.asyncio
async def test_a_token_that_names_no_workspace_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-with-at-least-32-bytes")
    resolver = EntitledScopeResolver(lambda workspace, repository: "github")

    with pytest.raises(ValueError, match="authenticated principal is required"):
        resolver(Scope.from_mapping({"repository": "acme/shop"}))
