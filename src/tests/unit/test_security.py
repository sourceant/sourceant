import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
import os
import hmac
import hashlib

# Set environment variables for testing
os.environ["REQUIRE_WEBHOOK_SECRET"] = "false"
os.environ["WEBHOOK_SECRET"] = "test_secret"
os.environ["SA_API_KEY"] = "test_api_key"

# Import the app after setting the environment variables
from src.api.main import app
from src.models.repository import Repository


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_repo():
    repo = Repository(
        provider="github",
        name="test-repo",
        full_name="test-owner/test-repo",
        url="https://github.com/test-owner/test-repo",
        private=False,
        archived=False,
        visibility="public",
        owner="test-owner",
        owner_type="User",
        default_branch="main",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T00:00:00Z",
    )
    return repo


def generate_signature(payload, secret):
    hash_payload = hmac.new(secret.encode(), payload.encode(), hashlib.sha256)
    return f"sha256={hash_payload.hexdigest()}"


# --- Webhook Security Tests ---


@patch.dict(os.environ, {"REQUIRE_WEBHOOK_SECRET": "true"})
def test_webhook_valid_signature_and_authorized_repo(client, mock_repo):
    import importlib
    from src.api.routes import pr
    importlib.reload(pr)

    with patch("src.models.repository.Repository.get_by_full_name") as mock_get_repo:
        mock_get_repo.return_value = mock_repo
        payload = '{"repository": {"full_name": "test-owner/test-repo"}}'
        signature = generate_signature(payload, "test_secret")

        response = client.post(
            "/api/prs/github-webhook",
            content=payload,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
            },
        )
        assert response.status_code == 201
    
    # Restore original state
    os.environ["REQUIRE_WEBHOOK_SECRET"] = "false"
    importlib.reload(pr)


@patch.dict(os.environ, {"REQUIRE_WEBHOOK_SECRET": "true"})
def test_webhook_invalid_signature(client):
    import importlib
    from src.api.routes import pr
    importlib.reload(pr)

    payload = '{"repository": {"full_name": "test-owner/test-repo"}}'
    signature = "sha256=invalid_signature"

    response = client.post(
        "/api/prs/github-webhook",
        content=payload,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid GitHub signature"

    # Restore original state
    os.environ["REQUIRE_WEBHOOK_SECRET"] = "false"
    importlib.reload(pr)


@patch.dict(os.environ, {"REQUIRE_WEBHOOK_SECRET": "true"})
def test_webhook_missing_signature_when_required(client):
    # Reload the module to pick up the changed env var
    import importlib
    from src.api.routes import pr
    importlib.reload(pr)

    payload = '{"repository": {"full_name": "test-owner/test-repo"}}'
    response = client.post(
        "/api/prs/github-webhook",
        content=payload,
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert response.status_code == 400
    assert "header is required" in response.json()["detail"]

    # Restore original state
    os.environ["REQUIRE_WEBHOOK_SECRET"] = "false"
    importlib.reload(pr)


@patch.dict(os.environ, {"REQUIRE_WEBHOOK_SECRET": "true"})
def test_webhook_unauthorized_repo(client):
    import importlib
    from src.api.routes import pr
    importlib.reload(pr)

    with patch("src.models.repository.Repository.get_by_full_name") as mock_get_repo:
        mock_get_repo.return_value = None  # Simulate repo not found
        payload = '{"repository": {"full_name": "unauthorized/repo"}}'
        signature = generate_signature(payload, "test_secret")

        response = client.post(
            "/api/prs/github-webhook",
            content=payload,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
            },
        )
        assert response.status_code == 403
        assert "not authorized" in response.json()["detail"]

    # Restore original state
    os.environ["REQUIRE_WEBHOOK_SECRET"] = "false"
    importlib.reload(pr)


def test_webhook_secret_not_required_by_default(client, mock_repo):
    with patch("src.models.repository.Repository.get_by_full_name") as mock_get_repo:
        mock_get_repo.return_value = mock_repo
        payload = '{"repository": {"full_name": "test-owner/test-repo"}}'

        response = client.post(
            "/api/prs/github-webhook",
            content=payload,
            headers={
                "X-GitHub-Event": "pull_request",
                # No signature header
            },
        )
        assert response.status_code == 201


# --- API Key Security Tests ---


def test_protected_endpoint_valid_api_key(client):
    with patch(
        "src.controllers.repository_event_controller.RepositoryEventController.index"
    ) as mock_index:
        mock_index.return_value = []
        response = client.get(
            "/repository-events", headers={"X-SourceAnt-API-KEY": "test_api_key"}
        )
        assert response.status_code == 200


def test_protected_endpoint_invalid_api_key(client):
    response = client.get(
        "/repository-events", headers={"X-SourceAnt-API-KEY": "invalid_key"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API Key"


def test_protected_endpoint_missing_api_key(client):
    response = client.get("/repository-events")
    assert response.status_code == 422  # FastAPI's response for missing header


@patch.dict(os.environ, {"SA_API_KEY": ""})
def test_protected_endpoint_server_key_not_configured(client):
    # Reload the security module to pick up the changed env var
    import importlib
    from src.api import security

    importlib.reload(security)

    response = client.get(
        "/repository-events", headers={"X-SourceAnt-API-KEY": "any_key"}
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "API Key not configured on server."

    # Restore original state
    os.environ["SA_API_KEY"] = "test_api_key"
    importlib.reload(security)
