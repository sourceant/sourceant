from pathlib import Path

import pytest

from src.core.topology.inference import (
    DEPENDS_ON,
    RepositoryManifest,
    infer_dependencies,
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
                    identity=("@acme/web",),
                    dependencies=("@acme/design", "lodash"),
                ),
                RepositoryManifest(
                    entity_id="asset:design",
                    repository="acme/design",
                    path="package.json",
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
                    dependencies=("lodash", "nuxt"),
                )
            ]
        )

        assert proposals == ()

    def test_a_name_two_repositories_publish_proposes_nothing(self):
        """Taken from these repositories: the dashboard and memory both call
        themselves sourceant-memory, so a dependency on that name cannot say
        which one is meant."""
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

        caller = RepositoryManifest(
            entity_id="asset:core",
            repository="sourceant/sourceant",
            path="requirements.txt",
            dependencies=("sourceant-memory",),
        )

        assert infer_dependencies([dashboard, memory, caller]) == ()

    def test_a_repository_depending_on_itself_proposes_nothing(self):
        proposals = infer_dependencies(
            [
                RepositoryManifest(
                    entity_id="asset:web",
                    repository="acme/web",
                    path="package.json",
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
                    dependencies=("@acme/design",),
                ),
                RepositoryManifest(
                    entity_id="asset:web",
                    repository="acme/web",
                    path="apps/admin/package.json",
                    dependencies=("@acme/design",),
                ),
                RepositoryManifest(
                    entity_id="asset:design",
                    repository="acme/design",
                    path="package.json",
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
                    dependencies=("b",),
                ),
                RepositoryManifest(
                    entity_id="b",
                    repository="o/b",
                    path="package.json",
                    identity=("b",),
                ),
            ]
        )

        assert all(p.status == status for p in proposals)
