"""What a system holds, asked of the thing that owns the answer.

Deciding whether an asset is inside a system meant reading the whole graph and
following the containment edges by hand. A caller doing that holds the whole
graph to answer a question about two identities.
"""

from src.core.scope import Scope
from src.core.topology import (
    InMemoryTopologyRepository,
    SystemContents,
    TopologyEntity,
    TopologyRelationship,
    contents,
)

WHERE = Scope.from_mapping({"workspace": "1"})


def graph(*edges, systems=(), assets=()):
    repository = InMemoryTopologyRepository()
    for name in systems:
        repository.put_entity(WHERE, TopologyEntity(name, "system", "approved"))
    for name in assets:
        repository.put_entity(WHERE, TopologyEntity(name, "service", "approved"))
    for parent, child in edges:
        repository.put_relationship(
            WHERE,
            TopologyRelationship(
                f"{parent}->{child}", parent, child, "contains", "approved"
            ),
        )
    return repository


class TestWhatASystemHolds:
    def test_its_own_parts(self):
        held = contents(
            graph(
                ("stack", "api"),
                ("stack", "web"),
                systems=("stack",),
                assets=("api", "web"),
            ),
            WHERE,
            "stack",
        )

        assert held.assets == ("api", "web")
        assert held.systems == ()

    def test_the_parts_of_the_systems_it_holds(self):
        repository = graph(
            ("stack", "billing"),
            ("billing", "ledger"),
            systems=("stack", "billing"),
            assets=("ledger",),
        )

        held = contents(repository, WHERE, "stack")

        assert held.systems == ("billing",)
        assert held.assets == ("ledger",)

    def test_deeper_than_a_traversal_reaches(self):
        """A traversal stops at three hops and a hierarchy does not."""
        edges = [(f"s{n}", f"s{n + 1}") for n in range(6)]
        repository = graph(
            *edges,
            ("s6", "leaf"),
            systems=tuple(f"s{n}" for n in range(7)),
            assets=("leaf",),
        )

        held = contents(repository, WHERE, "s0")

        assert "s6" in held.systems
        assert held.assets == ("leaf",)
        assert not held.truncated

    def test_a_system_holding_nothing(self):
        assert contents(graph(systems=("stack",)), WHERE, "stack") == SystemContents()

    def test_nothing_above_it_is_counted(self):
        """Containment runs one way. A parent is not inside its child."""
        repository = graph(("stack", "billing"), systems=("stack", "billing"))

        assert contents(repository, WHERE, "billing").systems == ()

    def test_a_sibling_is_not_inside(self):
        repository = graph(
            ("stack", "billing"),
            ("stack", "web"),
            systems=("stack", "billing", "web"),
        )

        assert contents(repository, WHERE, "billing").systems == ()


class TestSayingWhenItStoppedEarly:
    def test_a_hierarchy_deeper_than_it_will_walk(self):
        """Deciding membership from a truncated answer refuses things that
        are in fact inside, so the caller is told."""
        edges = [(f"s{n}", f"s{n + 1}") for n in range(5)]
        repository = graph(*edges, systems=tuple(f"s{n}" for n in range(6)))

        held = contents(repository, WHERE, "s0", depth=2)

        assert held.truncated
        assert "s5" not in held.systems

    def test_a_walk_that_finished_says_so(self):
        repository = graph(("stack", "api"), systems=("stack",), assets=("api",))

        assert not contents(repository, WHERE, "stack").truncated
