from pathlib import Path

import pytest

from src.core.topology.inference import (
    COMPOSER,
    DEPENDS_ON,
    GOLANG,
    NPM,
    PYPI,
    RepositoryManifest,
    infer_dependencies,
    normalise,
    parse_manifest,
)

# Manifests copied from the repositories themselves, so the parsers are held to
# what these projects actually publish rather than to an invented shape.
MANIFESTS = Path(__file__).parent / "data" / "manifests"


def read(name: str) -> str:
    return (MANIFESTS / name).read_text()


class TestParsingRealManifests:
    def test_reads_a_node_package(self):
        manifest = parse_manifest(
            "asset:dashboard",
            "sourceant/dashboard",
            "package.json",
            read("dashboard.package.json"),
        )

        assert manifest.identity == ("sourceant-memory",)
        assert "nuxt" in manifest.dependencies
        # Development dependencies are declarations too.
        assert "vitest" in manifest.dependencies

    def test_reads_a_php_package_without_platform_requirements(self):
        manifest = parse_manifest(
            "asset:webservice",
            "sourceant/webservice",
            "composer.json",
            read("webservice.composer.json"),
        )

        assert manifest.identity == ("laravel/laravel",)
        assert "laravel/framework" in manifest.dependencies
        assert "whilesmart/eloquent-roles" in manifest.dependencies
        # "php" is a platform requirement, not a repository.
        assert "php" not in manifest.dependencies

    def test_reads_a_python_project(self):
        manifest = parse_manifest(
            "asset:memory",
            "sourceant/memory",
            "pyproject.toml",
            read("memory.pyproject.toml"),
        )

        assert manifest.identity == ("sourceant-memory",)

    def test_reads_a_requirements_file_without_its_options(self):
        manifest = parse_manifest(
            "asset:core",
            "sourceant/sourceant",
            "requirements.txt",
            read("sourceant.requirements.txt"),
        )

        assert "fastapi" in manifest.dependencies
        assert "alembic" in manifest.dependencies
        # A trailing comment is not part of the name.
        assert "cryptography" in manifest.dependencies
        assert not any(d.startswith("-") for d in manifest.dependencies)

    def test_ignores_a_file_that_is_not_a_manifest(self):
        assert parse_manifest("asset:x", "o/r", "README.md", "# hello") is None

    def test_a_manifest_that_will_not_parse_proposes_nothing(self):
        assert parse_manifest("asset:x", "o/r", "package.json", "{ not json") is None


class TestProposingDependencies:
    def test_proposes_a_pending_relationship_with_the_manifest_behind_it(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:web",
                    repository="acme/web",
                    path="package.json",
                    ecosystem=NPM,
                    identity=("@acme/web",),
                    dependencies=("@acme/design", "lodash"),
                ),
                RepositoryManifest(
                    entity_id="asset:design",
                    repository="acme/design",
                    path="package.json",
                    ecosystem=NPM,
                    identity=("@acme/design",),
                ),
            ]
        )

        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.source_id == "asset:web"
        assert proposal.target_id == "asset:design"
        assert proposal.type == DEPENDS_ON
        # Never applied without a person deciding.
        assert proposal.status == "pending"
        assert proposal.evidence[0].source == "acme/web/package.json"
        assert proposal.evidence[0].properties["declared"] == "@acme/design"

    def test_a_dependency_on_nothing_in_the_system_proposes_nothing(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:web",
                    repository="acme/web",
                    path="package.json",
                    ecosystem=NPM,
                    dependencies=("lodash", "nuxt"),
                )
            ]
        )

        assert proposals == ()

    def test_the_same_name_in_two_ecosystems_is_two_packages(self):
        """Taken from these repositories: the dashboard publishes
        sourceant-memory to npm and memory publishes it to pypi. A python
        dependency on that name can only mean the python one."""
        dashboard = parse_manifest(
            "asset:dashboard",
            "sourceant/dashboard",
            "package.json",
            read("dashboard.package.json"),
        )
        memory = parse_manifest(
            "asset:memory",
            "sourceant/memory",
            "pyproject.toml",
            read("memory.pyproject.toml"),
        )
        assert dashboard.identity == memory.identity
        assert dashboard.ecosystem != memory.ecosystem

        caller = RepositoryManifest(
            entity_id="asset:core",
            repository="sourceant/sourceant",
            path="requirements.txt",
            ecosystem=PYPI,
            dependencies=("sourceant-memory",),
        )

        proposals = infer_dependencies([dashboard, memory, caller])

        assert len(proposals) == 1
        assert proposals[0].target_id == "asset:memory"

    def test_the_same_name_twice_in_one_ecosystem_proposes_nothing(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="a",
                    repository="o/a",
                    path="package.json",
                    ecosystem=NPM,
                    identity=("shared",),
                ),
                RepositoryManifest(
                    entity_id="b",
                    repository="o/b",
                    path="package.json",
                    ecosystem=NPM,
                    identity=("shared",),
                ),
                RepositoryManifest(
                    entity_id="c",
                    repository="o/c",
                    path="package.json",
                    ecosystem=NPM,
                    dependencies=("shared",),
                ),
            ]
        )

        assert proposals == ()

    def test_a_repository_depending_on_itself_proposes_nothing(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:web",
                    repository="acme/web",
                    path="package.json",
                    ecosystem=NPM,
                    identity=("@acme/web",),
                    dependencies=("@acme/web",),
                )
            ]
        )

        assert proposals == ()

    def test_two_manifests_declaring_the_same_edge_gather_evidence_once(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:web",
                    repository="acme/web",
                    path="package.json",
                    ecosystem=NPM,
                    dependencies=("@acme/design",),
                ),
                RepositoryManifest(
                    entity_id="asset:web",
                    repository="acme/web",
                    path="apps/admin/package.json",
                    ecosystem=NPM,
                    dependencies=("@acme/design",),
                ),
                RepositoryManifest(
                    entity_id="asset:design",
                    repository="acme/design",
                    path="package.json",
                    ecosystem=NPM,
                    identity=("@acme/design",),
                ),
            ]
        )

        assert len(proposals) == 1
        assert len(proposals[0].evidence) == 2

    @pytest.mark.parametrize("status", ["pending"])
    def test_every_proposal_is_pending(self, status):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="a",
                    repository="o/a",
                    path="package.json",
                    ecosystem=NPM,
                    dependencies=("b",),
                ),
                RepositoryManifest(
                    entity_id="b",
                    repository="o/b",
                    path="package.json",
                    ecosystem=NPM,
                    identity=("b",),
                ),
            ]
        )

        assert all(p.status == status for p in proposals)


class TestNameComparison:
    """The rules each ecosystem compares names by, as the Package URL
    specification records them."""

    def test_python_ignores_case_and_separator_style(self):
        # PEP 503: runs of dot, hyphen and underscore collapse to one hyphen.
        for written in (
            "friendly-bard",
            "Friendly_Bard",
            "friendly.bard",
            "friendly--bard",
        ):
            assert normalise(PYPI, written) == "friendly-bard"

    def test_php_ignores_case(self):
        assert normalise(COMPOSER, "Laravel/Framework") == "laravel/framework"

    def test_node_and_go_compare_exactly(self):
        assert normalise(NPM, "MyPackage") == "MyPackage"
        assert normalise(GOLANG, "github.com/Acme/Thing") == "github.com/Acme/Thing"

    def test_a_python_dependency_written_either_way_finds_the_same_repository(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:lib",
                    repository="o/lib",
                    path="pyproject.toml",
                    ecosystem=PYPI,
                    identity=("Source_Ant.Memory",),
                ),
                RepositoryManifest(
                    entity_id="asset:app",
                    repository="o/app",
                    path="requirements.txt",
                    ecosystem=PYPI,
                    dependencies=("source-ant-memory",),
                ),
            ]
        )

        assert len(proposals) == 1
        assert proposals[0].target_id == "asset:lib"


class TestConfidence:
    """A name that says who owns it proves more than a bare word, because a
    public registry can carry the same bare word."""

    def test_an_owned_name_is_proposed_with_full_confidence(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:app",
                    repository="o/app",
                    path="composer.json",
                    ecosystem=COMPOSER,
                    dependencies=("acme/billing",),
                ),
                RepositoryManifest(
                    entity_id="asset:billing",
                    repository="o/billing",
                    path="composer.json",
                    ecosystem=COMPOSER,
                    identity=("acme/billing",),
                ),
            ]
        )

        assert proposals[0].confidence == 1.0
        assert proposals[0].evidence[0].properties["namespaced"] is True

    def test_a_bare_name_is_proposed_with_less(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:app",
                    repository="o/app",
                    path="requirements.txt",
                    ecosystem=PYPI,
                    dependencies=("billing",),
                ),
                RepositoryManifest(
                    entity_id="asset:billing",
                    repository="o/billing",
                    path="pyproject.toml",
                    ecosystem=PYPI,
                    identity=("billing",),
                ),
            ]
        )

        assert proposals[0].confidence < 1.0
        assert proposals[0].evidence[0].properties["namespaced"] is False
        # Still pending either way: confidence never decides.
        assert proposals[0].status == "pending"
