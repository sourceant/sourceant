import asyncio

import pytest

from src.core.code_index import (
    CodeEdge,
    CodeIndexReader,
    CodeNode,
    CodeSearch,
    CodeTraversal,
    InMemoryCodeIndex,
)
from src.core.knowledge import (
    InMemoryKnowledgeRepository,
    Knowledge,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeTraversal,
)
from src.core.plugins import (
    BasePlugin,
    EventHooks,
    PluginManager,
    PluginMetadata,
    PluginRegistry,
    PluginType,
)
from src.core.review_state import (
    FindingQuery,
    InMemoryReviewStateRepository,
    ReviewFinding,
)
from src.core.scope import Scope
from src.core.services import ServiceRegistry

PROJECT = Scope.from_mapping({"project": "one"})
OTHER_PROJECT = Scope.from_mapping({"project": "two"})


def code_node(identifier: str, **properties) -> CodeNode:
    return CodeNode(identifier, frozenset({"Function"}), properties)


def test_scope_is_immutable_extensible_and_order_independent():
    first = Scope.from_mapping({"repository": "one", "tenant": "two"})
    second = Scope((("tenant", "two"), ("repository", "one")))

    assert first == second
    assert first.get("tenant") == "two"
    assert first.extend({"revision": "abc"}).get("revision") == "abc"


def test_code_index_isolates_arbitrary_scopes():
    index = InMemoryCodeIndex()
    index.put_node(PROJECT, code_node("shared"))
    index.put_node(OTHER_PROJECT, code_node("shared"))

    result = index.traverse(CodeTraversal(PROJECT, ("shared",)))

    assert [node.id for node in result.nodes] == ["shared"]


def test_code_index_searches_open_labels_properties_and_pages():
    index = InMemoryCodeIndex()
    index.put_node(PROJECT, code_node("one", language="python"))
    index.put_node(PROJECT, code_node("two", language="python"))
    index.put_node(PROJECT, code_node("three", language="php"))

    result = index.search(
        CodeSearch(
            PROJECT,
            labels=frozenset({"Function"}),
            properties={"language": "python"},
            limit=1,
        )
    )

    assert [node.id for node in result.nodes] == ["one"]
    assert result.total == 2
    assert result.has_more is True


def test_code_index_traverses_cycles_with_limits():
    index = InMemoryCodeIndex()
    for identifier in ("a", "b", "c"):
        index.put_node(PROJECT, code_node(identifier))
    for source, target in (("a", "b"), ("b", "c"), ("c", "a")):
        index.put_edge(PROJECT, CodeEdge(f"{source}-{target}", source, target, "CALLS"))

    result = index.traverse(CodeTraversal(PROJECT, ("a",), depth=2, node_limit=2))

    assert len(result.nodes) == 2
    assert result.truncated is True


def test_code_index_replaces_edge_endpoints_and_obeys_direction():
    index = InMemoryCodeIndex()
    for identifier in ("a", "b", "c"):
        index.put_node(PROJECT, code_node(identifier))
    index.put_edge(PROJECT, CodeEdge("edge", "a", "b", "CALLS"))
    index.put_edge(PROJECT, CodeEdge("edge", "b", "c", "CUSTOM_RELATIONSHIP"))

    stale = index.traverse(CodeTraversal(PROJECT, ("a",)))
    inbound = index.traverse(CodeTraversal(PROJECT, ("c",), direction="inbound"))

    assert [node.id for node in stale.nodes] == ["a"]
    assert [node.id for node in inbound.nodes] == ["c", "b"]


def test_knowledge_queries_open_kinds_statuses_properties_and_pages():
    knowledge = InMemoryKnowledgeRepository()
    knowledge.put(PROJECT, Knowledge("one", "decision", "approved", "One"))
    knowledge.put(
        PROJECT,
        Knowledge("two", "custom-kind", "accepted", "Two", {"language": "python"}),
    )

    result = knowledge.search(
        KnowledgeQuery(
            PROJECT,
            kinds=frozenset({"custom-kind"}),
            statuses=frozenset({"accepted"}),
            properties={"language": "python"},
        )
    )

    assert [item.id for item in result.items] == ["two"]
    assert result.total == 1


def test_knowledge_relationships_remain_inside_scope():
    knowledge = InMemoryKnowledgeRepository()
    knowledge.put(PROJECT, Knowledge("one", "rule", "approved", "One"))
    knowledge.put(PROJECT, Knowledge("two", "rule", "approved", "Two"))
    knowledge.put_relationship(
        PROJECT,
        KnowledgeRelationship("edge", "one", "two", "CUSTOM", "accepted"),
    )

    relationships = knowledge.get_relationships(
        PROJECT,
        frozenset({"one", "two"}),
        frozenset({"accepted"}),
    )

    assert [relationship.id for relationship in relationships] == ["edge"]


def test_knowledge_traversal_is_bounded_filtered_and_scope_isolated():
    knowledge = InMemoryKnowledgeRepository()
    for scope in (PROJECT, OTHER_PROJECT):
        for identifier in ("a", "b", "c"):
            knowledge.put(
                scope,
                Knowledge(identifier, "decision", "approved", identifier),
            )
    knowledge.put_relationship(
        PROJECT,
        KnowledgeRelationship("a-b", "a", "b", "depends_on", "approved"),
    )
    knowledge.put_relationship(
        PROJECT,
        KnowledgeRelationship("b-c", "b", "c", "relates_to", "pending"),
    )

    result = knowledge.traverse(
        KnowledgeTraversal(
            PROJECT,
            ("a",),
            depth=3,
            relationship_types=frozenset({"depends_on"}),
            relationship_statuses=frozenset({"approved"}),
        )
    )

    assert [item.id for item in result.items] == ["a", "b"]
    assert [relationship.id for relationship in result.relationships] == ["a-b"]
    assert result.truncated is False


def test_knowledge_traversal_handles_cycles_directions_and_limits():
    knowledge = InMemoryKnowledgeRepository()
    for identifier in ("a", "b", "c"):
        knowledge.put(
            PROJECT,
            Knowledge(identifier, "decision", "approved", identifier),
        )
    for source, target in (("a", "b"), ("b", "c"), ("c", "a")):
        knowledge.put_relationship(
            PROJECT,
            KnowledgeRelationship(f"{source}-{target}", source, target, "relates_to"),
        )

    outbound = knowledge.traverse(
        KnowledgeTraversal(PROJECT, ("a",), depth=3, direction="outbound")
    )
    limited = knowledge.traverse(
        KnowledgeTraversal(PROJECT, ("a",), depth=3, knowledge_limit=2)
    )

    assert [item.id for item in outbound.items] == ["a", "b", "c"]
    assert len(outbound.relationships) == 3
    assert len(limited.items) == 2
    assert limited.truncated is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("depth", 4, "depth must be between 1 and 3"),
        ("knowledge_limit", 51, "knowledge_limit must be between 1 and 50"),
        (
            "relationship_limit",
            501,
            "relationship_limit must be between 1 and 500",
        ),
        ("direction", "sideways", "direction must be outbound, inbound, or both"),
    ],
)
def test_knowledge_traversal_rejects_unbounded_inputs(field, value, message):
    arguments = {"scope": PROJECT, "knowledge_ids": ("a",), field: value}

    with pytest.raises(ValueError, match=message):
        KnowledgeTraversal(**arguments)


def test_review_state_uses_scope_and_extensible_state():
    reviews = InMemoryReviewStateRepository()
    reviews.put_finding(PROJECT, ReviewFinding("one", "needs-review", "One"))
    reviews.put_finding(PROJECT, ReviewFinding("two", "resolved", "Two"))

    result = reviews.search(FindingQuery(PROJECT, frozenset({"needs-review"})))

    assert [finding.id for finding in result.findings] == ["one"]


class CodeIndexPlugin(BasePlugin):
    @property
    def metadata(self):
        return PluginMetadata(
            name="code-index",
            version="1",
            description="",
            author="",
            plugin_type=PluginType.UTILITY,
        )

    async def _register_services(self):
        self.services.register(CodeIndexReader, InMemoryCodeIndex(), self.metadata.name)


def test_plugin_manager_binds_runtime_services(tmp_path):
    services = ServiceRegistry()
    manager = PluginManager(
        registry=PluginRegistry(),
        hooks=EventHooks(),
        services=services,
    )
    package = tmp_path / "code_index_plugin"
    package.mkdir()
    (package / "__init__.py").write_text("")
    plugin_path = package / "plugin.py"
    plugin_path.write_text(
        "from src.tests.unit.test_core_memory_services import CodeIndexPlugin\n"
    )

    plugin = asyncio.run(manager.load_plugin(plugin_path, "code_index_plugin"))
    assert plugin is not None
    asyncio.run(plugin.initialize())
    assert isinstance(services.resolve(CodeIndexReader), InMemoryCodeIndex)

    asyncio.run(plugin.cleanup())
    with pytest.raises(LookupError, match="CodeIndexReader"):
        services.resolve(CodeIndexReader)
