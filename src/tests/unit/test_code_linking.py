"""Working out which file an import names."""

from src.core.code_index.linking import index_directories, index_paths, resolve

PATHS = [
    "src/core/scope.py",
    "src/core/code_index/__init__.py",
    "src/core/code_index/emit.py",
    "src/api/routes/code.py",
    "ui/src/components/CodeGraph.vue",
    "ui/src/api.js",
    "internal/core/client.go",
    "internal/core/index.ts",
    "cmd/agent/main.go",
]
REPOSITORY = index_paths(PATHS)
INSIDE = index_directories(PATHS)


def only(importer, source):
    found = resolve(REPOSITORY, importer, source, INSIDE)
    return found[0] if len(found) == 1 else (found or None)


class TestRelative:
    def test_a_sibling_is_the_file_next_to_it(self):
        assert only("ui/src/components/CodeGraph.vue", "../api") == "ui/src/api.js"

    def test_a_dot_is_the_same_directory(self):
        assert only("src/core/code_index/emit.py", "./__init__") == (
            "src/core/code_index/__init__.py"
        )

    def test_climbing_past_the_root_resolves_to_nothing(self):
        assert only("src/api.py", "../../../elsewhere") is None

    # `./charge` in one directory is not `charge` in another, and joining them
    # would draw a line between two files that have nothing to do with one
    # another.
    def test_a_relative_import_does_not_wander(self):
        assert only("cmd/agent/main.go", "./scope") is None


class TestDotted:
    def test_a_dotted_module_is_a_path(self):
        assert only("src/api/routes/code.py", "src.core.scope") == "src/core/scope.py"

    def test_a_package_is_the_file_that_opens_it(self):
        assert only("src/api/routes/code.py", "src.core.code_index") == (
            "src/core/code_index/__init__.py"
        )


class TestQualified:
    def test_a_package_qualified_import_matches_on_its_end(self):
        """The host and account in front of it are not in the repository.

        `internal/core` resolves to the file that opens that directory, not to
        the other file sitting in it.
        """
        assert only("cmd/agent/main.go", "example.com/who/what/internal/core") == (
            "internal/core/index.ts"
        )

    def test_the_longest_end_that_matches_wins(self):
        assert only("cmd/agent/main.go", "example.com/who/internal/core/client") == (
            "internal/core/client.go"
        )

    def test_a_bare_name_is_not_enough_to_go_on(self):
        """`utils` names nothing in particular, and a wrong line reads as fact."""
        assert only("cmd/agent/main.go", "fmt") is None


class TestRefusals:
    def test_nothing_resolves_to_nothing(self):
        assert only("src/core/scope.py", "") is None

    def test_a_file_does_not_import_itself(self):
        assert only("src/core/scope.py", "src.core.scope") is None

    def test_a_name_two_files_answer_to_is_left_alone(self):
        """Two files could be meant, so no line is drawn rather than the wrong one."""
        ambiguous = index_paths(["a/config.py", "b/config.py"])

        assert resolve(ambiguous, "main.py", "config") == ()


class TestPackages:
    """Some languages import a directory, and the package is every file in it."""

    def test_importing_a_directory_reaches_everything_in_it(self):
        paths = ["internal/core/client.go", "internal/core/store.go", "cmd/main.go"]

        found = resolve(
            index_paths(paths),
            "cmd/main.go",
            "example.com/who/internal/core",
            index_directories(paths),
        )

        assert found == ("internal/core/client.go", "internal/core/store.go")

    def test_a_package_does_not_reach_back_to_the_file_importing_it(self):
        paths = ["internal/core/client.go", "internal/core/store.go"]

        found = resolve(
            index_paths(paths),
            "internal/core/client.go",
            "internal/core",
            index_directories(paths),
        )

        assert found == ("internal/core/store.go",)


class TestNaming:
    def test_a_file_is_known_by_its_path_and_without_its_extension(self):
        names = index_paths(["src/core/scope.py"])

        assert names["src/core/scope.py"] == ("src/core/scope.py",)
        assert names["src/core/scope"] == ("src/core/scope.py",)

    def test_a_directory_answers_for_the_file_that_opens_it(self):
        names = index_paths(["src/core/code_index/__init__.py"])

        assert names["src/core/code_index"] == ("src/core/code_index/__init__.py",)
