"""Finding the parts of a graph that belong together, and naming them."""

from src.core.code_index import CodeEdge, CodeNode

from src.core.code_index.clustering import (
    GraphClustering,
    LabelPropagation,
    Modularity,
    degrees,
)


def node(identifier, path="src/a.py", name=None):
    return CodeNode(
        identifier,
        frozenset({"Function"}),
        {"name": name or identifier.rsplit(".", 1)[-1], "file_path": path},
    )


def joined(pairs):
    return [CodeEdge(str(i), s, t, "CALLS") for i, (s, t) in enumerate(pairs)]


def clump(prefix, path, size=5):
    """A set of nodes that all call each other."""
    ids = [f"{prefix}.{n}" for n in range(size)]
    nodes = [node(i, path) for i in ids]
    edges = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]]
    return nodes, edges


class TestFindingTheParts:
    def test_two_clumps_joined_by_one_thread_stay_two(self):
        left, left_edges = clump("auth", "src/auth/handler.py")
        right, right_edges = clump("view", "src/views/page.py")
        edges = joined(left_edges + right_edges + [("auth.0", "view.0")])

        found = Modularity().cluster(left + right, edges)

        assert len({found.of[n.id] for n in left}) == 1
        assert len({found.of[n.id] for n in right}) == 1
        assert found.of["auth.0"] != found.of["view.0"]

    def test_the_biggest_part_is_first(self):
        """So the busiest part of a graph keeps its colour as the graph grows."""
        small, small_edges = clump("small", "src/small/a.py", size=3)
        big, big_edges = clump("big", "src/big/a.py", size=8)

        found = Modularity().cluster(small + big, joined(small_edges + big_edges))

        assert found.communities[0].size == 8
        assert found.of["big.0"] == 0

    def test_the_same_graph_gives_the_same_answer(self):
        """Moving nodes one at a time depends on the order they are visited. If
        the colours moved between two looks at the same graph, the drawing would
        not be worth looking at twice."""
        nodes, edges = clump("a", "src/a/x.py")
        more, more_edges = clump("b", "src/b/x.py")
        graph = (nodes + more, joined(edges + more_edges + [("a.0", "b.0")]))

        first = Modularity().cluster(*graph)
        second = Modularity().cluster(*graph)

        assert first.of == second.of
        assert [c.name for c in first.communities] == [
            c.name for c in second.communities
        ]

    def test_something_nothing_touches_is_its_own_part(self):
        nodes, edges = clump("a", "src/a/x.py", size=3)
        alone = node("lonely", "src/z/z.py")

        found = Modularity().cluster(nodes + [alone], joined(edges))

        assert found.of["lonely"] not in {found.of[n.id] for n in nodes}

    def test_both_implementations_satisfy_the_protocol(self):
        """The point of the protocol is that another algorithm can be dropped
        in without the caller changing."""
        assert isinstance(Modularity(), GraphClustering)
        assert isinstance(LabelPropagation(), GraphClustering)


class TestNamingThem:
    def test_a_part_is_named_for_where_it_lives(self):
        nodes, edges = clump("auth", "src/auth/handler.py")

        found = Modularity().cluster(nodes, joined(edges))

        assert found.communities[0].name == "src/auth"

    def test_a_part_spread_across_the_tree_is_named_for_its_busiest_symbol(self):
        spread = [
            node("a", "one/a.py"),
            node("b", "two/b.py"),
            node("hub", "three/c.py", name="Dispatcher"),
            node("d", "four/d.py"),
        ]
        found = Modularity().cluster(
            spread, joined([("a", "hub"), ("b", "hub"), ("d", "hub")])
        )

        assert found.communities[0].name == "Dispatcher"

    def test_no_two_parts_share_a_name(self):
        """Several parts can sit in one folder, and a legend listing the same
        folder five times tells the reader nothing."""
        first, first_edges = clump("one", "src/core/a.py")
        second, second_edges = clump("two", "src/core/b.py")

        found = Modularity().cluster(first + second, joined(first_edges + second_edges))

        names = [c.name for c in found.communities]
        assert len(names) == len(set(names))
        assert "src/core" in names


class TestCountingTheLines:
    def test_degree_counts_both_ends(self):
        nodes = [node("a"), node("b"), node("c")]
        counted = degrees(nodes, joined([("a", "b"), ("a", "c")]))

        assert counted == {"a": 2, "b": 1, "c": 1}

    def test_a_line_to_something_not_drawn_is_not_counted(self):
        counted = degrees([node("a")], joined([("a", "elsewhere")]))

        assert counted == {"a": 1}
