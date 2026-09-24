import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes import code
from src.core.code_index import SQLCodeIndexRepository


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(code, "LOCAL_MODE", True)
    index = SQLCodeIndexRepository(
        create_engine(f"sqlite:///{tmp_path / 'index.db'}"), create_schema=True
    )
    app.dependency_overrides[code.get_code_index] = lambda: index
    source = tmp_path / "billing"
    (source / "payments").mkdir(parents=True)
    (source / "identity").mkdir()
    (source / "payments" / "charge.py").write_text(
        "from identity.user import user\n\ndef charge():\n    return user()\n"
    )
    (source / "identity" / "user.py").write_text("def user():\n    return 1\n")
    client = TestClient(app)
    assert (
        client.post(
            "/api/code/repositories", json={"path": str(source), "name": "acme/billing"}
        ).status_code
        == 200
    )
    assert (
        client.post("/api/code/index", json={"repository": "acme/billing"}).status_code
        == 200
    )
    yield client, source, index
    app.dependency_overrides.pop(code.get_code_index, None)


def read(client):
    response = client.get(
        "/api/code/architecture", params={"repository": "acme/billing"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_snapshot_has_stable_components_and_bounded_evidence(indexed):
    client, source, _ = indexed
    before = read(client)
    parts = {part["name"]: part for part in before["components"]}
    assert set(parts) == {"identity", "payments"}
    edge = before["relationships"][0]
    assert (edge["source"], edge["target"]) == (
        parts["payments"]["id"],
        parts["identity"]["id"],
    )
    assert edge["evidence"][0]["source"]["path"] == "payments/charge.py"
    assert edge["evidence"][0]["origin"] == "inferred"
    assert read(client) == before
    (source / "reports").mkdir()
    (source / "reports" / "report.py").write_text("def report():\n    return 1\n")
    client.post("/api/code/index", json={"repository": "acme/billing"})
    after = read(client)
    assert {part["name"]: part for part in after["components"]}["payments"] == parts[
        "payments"
    ]
    compared = client.post("/api/code/architecture/compare", json=before)
    assert compared.status_code == 200, compared.text
    changes = compared.json()["data"]
    assert [(part["name"], part["status"]) for part in changes["components"]] == [
        ("reports", "added")
    ]
    assert changes["relationships"] == []


def test_body_change_changes_own_component_only(indexed):
    client, source, _ = indexed
    before = read(client)
    (source / "identity" / "user.py").write_text("def user():\n    return 2\n")
    client.post("/api/code/index", json={"repository": "acme/billing"})
    response = client.post("/api/code/architecture/compare", json=before)
    assert response.status_code == 200
    assert [
        (part["name"], part["status"]) for part in response.json()["data"]["components"]
    ] == [("identity", "modified")]


def test_removed_dependency_is_reported(indexed):
    client, source, _ = indexed
    before = read(client)
    (source / "payments" / "charge.py").write_text("def charge():\n    return 0\n")
    client.post("/api/code/index", json={"repository": "acme/billing"})
    response = client.post("/api/code/architecture/compare", json=before)
    assert response.status_code == 200
    assert response.json()["data"]["relationships"][0]["status"] == "removed"


def test_incomplete_or_invalid_baselines_cannot_claim_removals(indexed):
    client, _, _ = indexed
    before = read(client)
    before["coverage"]["truncated"] = True
    assert client.post("/api/code/architecture/compare", json=before).status_code == 409
    before["schema_version"] = 2
    assert client.post("/api/code/architecture/compare", json=before).status_code == 422


def test_unregistered_repository_and_hosted_access_are_refused(indexed, monkeypatch):
    client, _, _ = indexed
    assert (
        client.get(
            "/api/code/architecture", params={"repository": "other/repo"}
        ).status_code
        == 404
    )
    before = read(client)
    before["repository"] = "other/repo"
    assert client.post("/api/code/architecture/compare", json=before).status_code == 404
    monkeypatch.setattr(code, "LOCAL_MODE", False)
    assert (
        client.get(
            "/api/code/architecture", params={"repository": "acme/billing"}
        ).status_code
        == 403
    )
    assert client.post("/api/code/architecture/compare", json=before).status_code == 403


def test_snapshot_round_trips(indexed):
    client, _, _ = indexed
    before = read(client)
    response = client.post("/api/code/architecture/compare", json=before)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["components"] == []
    assert response.json()["data"]["relationships"] == []
    destination = os.environ.get("SOURCEANT_ARCHITECTURE_CAPTURE")
    if destination:
        directory = Path(destination)
        (directory / "architecture.json").write_text(
            json.dumps(before, indent=2) + "\n"
        )
        (directory / "architecture-comparison.json").write_text(
            json.dumps(response.json()["data"], indent=2) + "\n"
        )


@pytest.mark.parametrize("malformed", ["duplicate", "dangling"])
def test_malformed_baseline_is_rejected(indexed, malformed):
    client, _, _ = indexed
    before = read(client)
    if malformed == "duplicate":
        before["components"].append(before["components"][0])
    else:
        before["relationships"][0]["source"] = "missing"
    assert client.post("/api/code/architecture/compare", json=before).status_code == 409
