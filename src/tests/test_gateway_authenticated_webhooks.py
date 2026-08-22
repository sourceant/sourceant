import time

import jwt
import pytest

from src.tests.base_test import BaseTestCase

SECRET = "a-jwt-secret-of-at-least-32-bytes-long"


def token(workspace_id="7", expires_in=60):
    return jwt.encode(
        {
            "sub": "installation:1",
            "scope": {"workspace_id": workspace_id, "repository_ids": []},
            "exp": int(time.time()) + expires_in,
        },
        SECRET,
        algorithm="HS256",
    )


class TestGatewayAuthenticatedWebhooks(BaseTestCase):
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", SECRET)

    def payload(self):
        return {
            "action": "opened",
            "pull_request": {
                "url": "https://api.github.com/repos/sourceant/sourceant/pulls/1",
                "title": "Fix bug",
                "number": 1,
            },
            "repository": {"full_name": "sourceant/sourceant"},
            "sender": {"login": "octocat"},
        }

    def deliver(self, headers=None):
        return self.client.post(
            "/api/prs/github-webhook",
            headers={"X-GitHub-Event": "pull_request", **(headers or {})},
            json=self.payload(),
        )

    def test_it_accepts_a_delivery_the_gateway_signed(self):
        assert self.deliver({"Authorization": f"Bearer {token()}"}).status_code == 201

    def test_it_rejects_a_token_signed_with_another_secret(self):
        forged = jwt.encode(
            {"sub": "installation:1", "exp": int(time.time()) + 60},
            "not-the-secret-at-all-but-long-enough",
            algorithm="HS256",
        )

        assert self.deliver({"Authorization": f"Bearer {forged}"}).status_code == 401

    def test_it_rejects_an_expired_token(self):
        assert (
            self.deliver(
                {"Authorization": f"Bearer {token(expires_in=-10)}"}
            ).status_code
            == 401
        )

    def test_it_rejects_a_header_that_is_not_a_bearer_token(self):
        assert self.deliver({"Authorization": "Basic abc"}).status_code == 401

    def test_it_still_accepts_a_delivery_with_no_gateway_in_front(self):
        assert self.deliver().status_code == 201

    def test_it_refuses_an_unsigned_delivery_when_a_gateway_is_required(
        self, monkeypatch
    ):
        monkeypatch.setattr("src.api.routes.pr.REQUIRE_GATEWAY", True)

        assert self.deliver().status_code == 401

    def test_it_accepts_a_signed_delivery_when_a_gateway_is_required(self, monkeypatch):
        monkeypatch.setattr("src.api.routes.pr.REQUIRE_GATEWAY", True)

        assert self.deliver({"Authorization": f"Bearer {token()}"}).status_code == 201
