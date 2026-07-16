from io import BytesIO

import pytest

from src.core.scope import Scope
from src.core.storage import (
    ArtifactKey,
    ArtifactStore,
    ArtifactWrite,
    ContentDigest,
    FileSystemArtifactStore,
    FileSystemWorkingAreaStore,
    WorkingAreaRequest,
    WorkingAreaStore,
)

PROJECT = Scope.from_mapping({"project": "one", "tenant": "alpha"})
OTHER_PROJECT = Scope.from_mapping({"project": "one", "tenant": "beta"})


def test_filesystem_artifacts_are_immutable_and_scope_isolated(tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    key = ArtifactKey(PROJECT, "code-index", "repository", "abc123")
    other_key = ArtifactKey(OTHER_PROJECT, "code-index", "repository", "abc123")

    artifact = store.put(ArtifactWrite(key), BytesIO(b"graph"))
    store.put(ArtifactWrite(other_key), BytesIO(b"other"))

    assert artifact.size == 5
    assert store.open(key).read() == b"graph"
    assert [item.key for item in store.list(PROJECT, "code-index")] == [key]
    with pytest.raises(FileExistsError):
        store.put(ArtifactWrite(key), BytesIO(b"replacement"))


def test_filesystem_artifact_checks_expected_digest_before_publish(tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    key = ArtifactKey(PROJECT, "code-index", "repository", "abc123")
    request = ArtifactWrite(key, expected_digest=ContentDigest("sha256", "incorrect"))

    with pytest.raises(ValueError, match="expected digest"):
        store.put(request, BytesIO(b"graph"))

    assert store.get(key) is None


def test_filesystem_working_areas_are_stable_and_scope_isolated(tmp_path):
    store = FileSystemWorkingAreaStore(tmp_path)
    request = WorkingAreaRequest(PROJECT, "repository", "source")
    other_request = WorkingAreaRequest(OTHER_PROJECT, "repository", "source")

    area = store.provision(request)
    other_area = store.provision(other_request)

    assert area == store.provision(request)
    assert area.path != other_area.path
    assert tuple(store.list(PROJECT, "repository")) == (area,)
    assert store.remove(request) is True
    assert store.remove(request) is False


def test_storage_identifiers_cannot_escape_the_storage_root(tmp_path):
    artifacts = FileSystemArtifactStore(tmp_path / "artifacts")
    areas = FileSystemWorkingAreaStore(tmp_path / "areas")

    with pytest.raises(ValueError, match="single path components"):
        artifacts.put(
            ArtifactWrite(ArtifactKey(PROJECT, "code-index", "repository", "../bad")),
            BytesIO(b"graph"),
        )
    with pytest.raises(ValueError, match="single path components"):
        areas.provision(WorkingAreaRequest(PROJECT, "repository", "../bad"))


def test_filesystem_implementations_satisfy_storage_protocols(tmp_path):
    assert isinstance(FileSystemArtifactStore(tmp_path / "artifacts"), ArtifactStore)
    assert isinstance(FileSystemWorkingAreaStore(tmp_path / "areas"), WorkingAreaStore)
