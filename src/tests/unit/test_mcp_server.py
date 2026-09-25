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
from src.core.groups import CheckedGroups, SQLGroupsRepository
from src.core.requirements import Requirement, SQLRequirementsRepository
from src.core.requirements.grouping import GroupableRequirements
from src.core.scope import Scope
from src.core.topology import SQLTopologyRepository
from src.core.environment import LOCAL
from src.core.mcp import (
    contribute_tools,
    create_mcp_server,
    hosted_surface,
    personal_surface,
)
from src.core.services import ServiceRegistry
from src.plugins.builtin.code_reviewer.tools import ReviewTools
from src.mcp_server.application import create_http_mcp_server
from src.core.mcp.auth import EntitledScopeResolver, SourceAntTokenVerifier
from src.core.mcp.surface import Surface

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
        "put_group",
        "place_in_group",
        "unfile_from_group",
        "search_groups",
        "get_group_rollup",
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
async def test_mcp_requirement_coverage_exposes_truncation(tmp_path):
    requirements = SQLRequirementsRepository(
        create_engine(f"sqlite:///{tmp_path / 'requirements.db'}"),
        create_schema=True,
    )
    for index in range(101):
        requirements.put(
            PROJECT,
            Requirement(
                id=f"r{index:03}",
                kind="requirement",
                status="open",
                summary="Keep the behavior",
            ),
        )
    server = create_mcp_server(
        DefaultContextProvider(requirements=requirements),
        requirements=requirements,
        services=ServiceRegistry(),
    )

    async with create_connected_server_and_client_session(server) as session:
        names = {prompt.name for prompt in (await session.list_prompts()).prompts}
        assert "check_requirements" in names
        command = await session.get_prompt("check_requirements")
        assert "get_requirement_coverage" in command.messages[0].content.text
        result = await session.call_tool(
            "get_requirement_coverage",
            {"scope": {"project": "one"}},
        )

    assert result.isError is False
    assert len(result.structuredContent["items"]) == 100
    assert result.structuredContent["truncated"] is True


@pytest.mark.asyncio
async def test_mcp_groups_requirements_from_more_than_one_repository(tmp_path):
    requirements = SQLRequirementsRepository(
        create_engine(f"sqlite:///{tmp_path / 'requirements.db'}"),
        create_schema=True,
    )
    billing = PROJECT.extend({"repository": "acme/billing"})
    payments = PROJECT.extend({"repository": "acme/payments"})
    requirements.put(billing, Requirement("r1", "requirement", "open", "Refund fast"))
    requirements.put(payments, Requirement("r2", "requirement", "open", "Refund once"))
    filing = CheckedGroups(
        SQLGroupsRepository(
            create_engine(f"sqlite:///{tmp_path / 'groups.db'}"), create_schema=True
        ),
        (GroupableRequirements(requirements),),
    )
    server = create_mcp_server(
        DefaultContextProvider(requirements=requirements),
        requirements=requirements,
        groups=filing,
    )

    async with create_connected_server_and_client_session(server) as session:
        made = await session.call_tool(
            "put_group",
            {"scope": {"project": "one"}, "id": "refunds", "name": "Refunds"},
        )
        assert made.isError is False

        for identity, repository in (("r1", "acme/billing"), ("r2", "acme/payments")):
            placed = await session.call_tool(
                "place_in_group",
                {
                    "scope": {"project": "one"},
                    "group_id": "refunds",
                    "member_type": "requirement",
                    "member_id": identity,
                    "repository": repository,
                },
            )
            assert placed.isError is False

        rolled = await session.call_tool(
            "get_group_rollup",
            {"scope": {"project": "one"}, "group_ids": ["refunds"]},
        )
        found = await session.call_tool(
            "search_groups",
            {"scope": {"project": "one"}, "parent_ids": [""]},
        )

    assert rolled.structuredContent["items"][0]["counts"]["requirement"]["total"] == 2
    assert [item["id"] for item in found.structuredContent["items"]] == ["refunds"]


@pytest.mark.asyncio
async def test_a_group_is_filed_by_workspace_however_the_call_names_its_scope(tmp_path):
    """A workspace token rebuilds the scope, and a repository may be in it.

    Filing at whatever came back would put a group written from inside a
    repository in a different partition from the one a search without that
    repository reads, and a scope matches by equality. Both calls below name the
    same group.
    """
    requirements = SQLRequirementsRepository(
        create_engine(f"sqlite:///{tmp_path / 'requirements.db'}"),
        create_schema=True,
    )
    billing = Scope.from_mapping({"workspace": "acme", "repository": "acme/billing"})
    requirements.put(billing, Requirement("r1", "requirement", "open", "Refund fast"))
    filing = CheckedGroups(
        SQLGroupsRepository(
            create_engine(f"sqlite:///{tmp_path / 'groups.db'}"), create_schema=True
        ),
        (GroupableRequirements(requirements),),
    )

    # What the hosted resolver does: the workspace comes off the token, and a
    # repository is kept only when the caller named one.
    def as_hosted(scope: Scope) -> Scope:
        values = {"workspace": "acme"}
        if scope.get("repository"):
            values["repository"] = scope.get("repository")
        return Scope.from_mapping(values)

    server = create_mcp_server(
        DefaultContextProvider(requirements=requirements),
        requirements=requirements,
        groups=filing,
        surface=Surface(environment=LOCAL, requirement_scope_resolver=as_hosted),
    )

    async with create_connected_server_and_client_session(server) as session:
        made = await session.call_tool(
            "put_group",
            {
                "scope": {"repository": "acme/billing"},
                "id": "refunds",
                "name": "Refunds",
            },
        )
        placed = await session.call_tool(
            "place_in_group",
            {
                "scope": {},
                "group_id": "refunds",
                "member_type": "requirement",
                "member_id": "r1",
                "repository": "acme/billing",
            },
        )
        found = await session.call_tool("search_groups", {"scope": {}})
        rolled = await session.call_tool(
            "get_group_rollup", {"scope": {}, "group_ids": ["refunds"]}
        )

    assert made.isError is False
    assert placed.isError is False
    assert [item["id"] for item in found.structuredContent["items"]] == ["refunds"]
    assert rolled.structuredContent["items"][0]["counts"]["requirement"]["total"] == 1


@pytest.mark.asyncio
async def test_a_rollup_refuses_a_depth_or_a_count_it_will_not_answer(tmp_path):
    filing = CheckedGroups(
        SQLGroupsRepository(
            create_engine(f"sqlite:///{tmp_path / 'groups.db'}"), create_schema=True
        ),
        (),
    )
    server = create_mcp_server(DefaultContextProvider(), groups=filing)

    async with create_connected_server_and_client_session(server) as session:
        deep = await session.call_tool(
            "get_group_rollup",
            {"scope": {"project": "one"}, "group_ids": ["refunds"], "depth": 5000},
        )
        none = await session.call_tool(
            "get_group_rollup", {"scope": {"project": "one"}, "group_ids": []}
        )

    assert deep.isError is True
    assert "depth must be between 1 and 20" in deep.content[0].text
    assert none.isError is True


@pytest.mark.asyncio
async def test_mcp_refuses_to_group_something_nobody_owns(tmp_path):
    filing = CheckedGroups(
        SQLGroupsRepository(
            create_engine(f"sqlite:///{tmp_path / 'groups.db'}"), create_schema=True
        ),
        (),
    )
    server = create_mcp_server(DefaultContextProvider(), groups=filing)

    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool(
            "put_group",
            {"scope": {"project": "one"}, "id": "refunds", "name": "Refunds"},
        )
        refused = await session.call_tool(
            "place_in_group",
            {
                "scope": {"project": "one"},
                "group_id": "refunds",
                "member_type": "invoice",
                "member_id": "i1",
            },
        )

    assert refused.isError is True


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
@pytest.mark.parametrize(
    "resource_url, host, origin",
    [
        (
            "https://sourceant.example.com/mcp",
            "sourceant.example.com",
            "https://sourceant.example.com",
        ),
        (
            "https://sourceant.example.com/mcp",
            "sourceant.example.com:443",
            "https://sourceant.example.com:443",
        ),
        (
            "https://sourceant.example.com:8443/mcp",
            "sourceant.example.com:8443",
            "https://sourceant.example.com:8443",
        ),
    ],
)
async def test_streamable_http_serves_what_the_caller_is_entitled_to(
    tmp_path, monkeypatch, resource_url, host, origin
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
        surface=hosted_surface(
            scope_resolver=EntitledScopeResolver(
                lambda workspace, repository: entitlements.get((workspace, repository))
            ),
            auth=AuthSettings(
                issuer_url="https://issuer.example.com",
                resource_server_url=resource_url,
                required_scopes=["sourceant"],
            ),
            token_verifier=SourceAntTokenVerifier(
                issuer="https://issuer.example.com",
                audience="sourceant-mcp",
                required_scopes=frozenset({"sourceant"}),
            ),
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
            base_url=origin,
            headers={
                "Authorization": f"Bearer {token}",
                "Host": host,
                "Origin": origin,
            },
            follow_redirects=True,
        ) as client:
            refused = await client.post(
                "/mcp/", json={}, headers={"Host": "untrusted.example.com"}
            )
            assert refused.status_code == 421
            refused = await client.post(
                "/mcp/", json={}, headers={"Origin": "https://untrusted.example.com"}
            )
            assert refused.status_code == 403
            refused = await client.post(
                "/mcp/", json={}, headers={"Host": "sourceant.example.com:9443"}
            )
            assert refused.status_code == 421
            async with streamable_http_client(
                resource_url + "/", http_client=client
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


class TestReviewingAWorkingTree:
    """The half an agent can do: ask for one, and hand somebody the link."""

    def asked(self):
        """The plugin's tool provider, with the reviewing itself stubbed out."""
        seen = {}

        class Fake(ReviewTools):
            def _start(self, repository, title=""):
                seen["repository"] = repository
                seen["title"] = title
                return {
                    "id": "abc123",
                    "repository": repository,
                    "status": "running",
                    "path": "/reviews/abc123",
                    "url": "http://127.0.0.1:8930/reviews/abc123",
                }

        services = ServiceRegistry()
        contribute_tools(Fake(), "test", services)
        return services, seen

    @pytest.mark.asyncio
    async def test_a_server_with_no_checkout_does_not_offer_it(self):
        services, _ = self.asked()
        server = create_mcp_server(
            DefaultContextProvider(code=InMemoryCodeIndex()),
            surface=hosted_surface(
                auth=AuthSettings(
                    issuer_url="https://issuer.example.com",
                    resource_server_url="https://sourceant.example.com/mcp",
                    required_scopes=["sourceant"],
                ),
                token_verifier=SourceAntTokenVerifier(
                    issuer="https://issuer.example.com",
                    audience="sourceant",
                    required_scopes=frozenset({"sourceant"}),
                ),
                scope_resolver=lambda scope: scope,
            ),
            services=services,
        )

        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()

        assert "review_working_tree" not in {tool.name for tool in tools.tools}

    @pytest.mark.asyncio
    async def test_a_server_that_can_reach_one_offers_it(self):
        services, _ = self.asked()
        server = create_mcp_server(
            DefaultContextProvider(code=InMemoryCodeIndex()),
            surface=personal_surface(),
            services=services,
        )

        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()

        assert "review_working_tree" in {tool.name for tool in tools.tools}

    @pytest.mark.asyncio
    async def test_it_answers_with_a_link_rather_than_findings(self):
        services, seen = self.asked()
        server = create_mcp_server(
            DefaultContextProvider(code=InMemoryCodeIndex()),
            surface=personal_surface(),
            services=services,
        )

        async with create_connected_server_and_client_session(server) as session:
            answered = await session.call_tool(
                "review_working_tree",
                {"repository": "acme/billing", "title": "Retry"},
            )

        structured = answered.structuredContent
        assert seen["repository"] == "acme/billing"
        assert structured["id"] == "abc123"
        assert structured["url"].endswith("/reviews/abc123")
        # Running, not finished: whoever asked is not the one who reads it.
        assert structured["status"] == "running"
