import hashlib
import hmac

import pytest

from src.tests.base_test import BaseTestCase

SECRET = "a-webhook-secret"


class TestWebhookSignature(BaseTestCase):
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

    def deliver(self, body: bytes, signature=None):
        headers = {"X-GitHub-Event": "pull_request", "Content-Type": "application/json"}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return self.client.post(
            "/api/prs/github-webhook", headers=headers, content=body
        )

    def signed(self, body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    @pytest.fixture
    def body(self):
        import json

        return json.dumps(self.payload()).encode()

    def test_it_accepts_a_correctly_signed_delivery(self, monkeypatch, body):
        monkeypatch.setattr("src.api.routes.pr.GITHUB_SECRET", SECRET)

        assert self.deliver(body, self.signed(body, SECRET)).status_code == 201

    def test_it_rejects_a_delivery_signed_with_another_secret(self, monkeypatch, body):
        monkeypatch.setattr("src.api.routes.pr.GITHUB_SECRET", SECRET)

        assert self.deliver(body, self.signed(body, "wrong")).status_code == 400

    def test_it_rejects_an_unsigned_delivery_when_a_secret_is_configured(
        self, monkeypatch, body
    ):
        monkeypatch.setattr("src.api.routes.pr.GITHUB_SECRET", SECRET)

        assert self.deliver(body).status_code == 400

    def test_it_accepts_an_unsigned_delivery_when_no_secret_is_configured(
        self, monkeypatch, body
    ):
        monkeypatch.setattr("src.api.routes.pr.GITHUB_SECRET", None)

        assert self.deliver(body).status_code == 201

    def test_it_accepts_an_unsigned_delivery_when_the_secret_is_empty(
        self, monkeypatch, body
    ):
        monkeypatch.setattr("src.api.routes.pr.GITHUB_SECRET", "")

        assert self.deliver(body).status_code == 201
