import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jwt
from litellm.exceptions import AuthenticationError, BadRequestError

from src.core.notifications import publish, report_failure
from src.llms.litellm_provider import LiteLLMProvider
from src.tests.base_test import BaseTestCase


def headers(workspace="notification-ours", user="1"):
    token = jwt.encode(
        {
            "sub": user,
            "scope": {"workspace_id": workspace},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class TestNotifications(BaseTestCase):
    def test_a_failed_memory_webhook_creates_a_credit_alert_without_visiting_analytics(
        self, monkeypatch
    ):
        from src.api.routes import pr
        from src.core.plugins import event_hooks

        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")
        monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "request")
        monkeypatch.setattr(
            "src.controllers.repository_event_controller.STATELESS_MODE", False
        )
        monkeypatch.setattr(event_hooks, "_event_subscribers", {})
        monkeypatch.setattr(pr, "GITHUB_SECRET", "")
        fixtures = Path(__file__).parent / "fixtures"
        captured = json.loads(
            (fixtures / "deepseek-insufficient-balance.json").read_text()
        )
        payload = json.loads((fixtures / "deepseek/webhook.json").read_text())
        event_hooks.subscribe_to_events(
            "notification-memory-test",
            callback=lambda *args: {"error": captured["reported_error"]},
            event_types=["pull_request." + payload["action"]],
        )
        signed = {**headers("notification-webhook"), "X-GitHub-Event": "pull_request"}
        result = self.client.post(
            "/api/prs/github-webhook", json=payload, headers=signed
        )
        assert result.status_code == 201, result.text
        data = self.client.get(
            "/api/notifications", headers=headers("notification-webhook")
        ).json()["data"]
        assert data["unread_count"] == 1
        assert data["items"][0]["type"] == "model.credits_exhausted"

    def test_credit_failure_reaches_the_workspace_without_raw_provider_details(
        self, monkeypatch
    ):
        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")
        captured = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "deepseek-insufficient-balance.json"
            ).read_text()
        )
        error = BadRequestError(
            captured["message"],
            "deepseek/deepseek-chat",
            "deepseek",
            body=captured["body"],
        )
        monkeypatch.setattr(
            "litellm.completion", lambda **kwargs: (_ for _ in ()).throw(error)
        )
        provider = LiteLLMProvider(
            "deepseek/deepseek-chat",
            4096,
            attribution={"workspace": "notification-credit"},
        )
        try:
            provider._completion(model=provider.model, messages=[])
        except BadRequestError as caught:
            assert caught is error
        else:
            raise AssertionError("The provider failure was swallowed")

        listed = self.client.get(
            "/api/notifications", headers=headers("notification-credit")
        )
        assert listed.status_code == 200
        data = listed.json()["data"]
        assert data["unread_count"] == 1
        assert data["items"][0]["type"] == "model.credits_exhausted"
        assert data["items"][0]["action_url"] == "/dashboard/settings"
        assert "request_id" not in listed.text
        assert captured["body"]["error"]["message"] not in listed.text

    def test_memory_wrapped_credit_failures_use_the_same_notification(
        self, monkeypatch
    ):
        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")
        captured = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "deepseek-insufficient-balance.json"
            ).read_text()
        )
        report_failure(
            RuntimeError(captured["reported_error"]), workspace="notification-memory"
        )
        report_failure(
            RuntimeError(captured["reported_error"]), workspace="notification-memory"
        )
        data = self.client.get(
            "/api/notifications", headers=headers("notification-memory")
        ).json()["data"]
        assert len(data["items"]) == 1
        assert data["items"][0]["type"] == "model.credits_exhausted"
        assert data["items"][0]["occurrences"] == 2

    def test_workspace_isolation_and_per_user_read_status(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")
        identifier = publish(
            workspace="notification-ours",
            type="operation.failed",
            title="Operation needs attention",
            message="Retry the operation.",
        )
        assert (
            self.client.get(
                "/api/notifications", headers=headers("notification-theirs")
            ).json()["data"]["items"]
            == []
        )
        assert (
            self.client.post(
                f"/api/notifications/{identifier}/read",
                headers=headers("notification-theirs"),
            ).status_code
            == 404
        )
        for _ in range(2):
            assert (
                self.client.post(
                    f"/api/notifications/{identifier}/read", headers=headers()
                ).status_code
                == 200
            )
        assert (
            self.client.get(
                "/api/notifications?unread_only=true", headers=headers()
            ).json()["data"]["unread_count"]
            == 0
        )
        assert (
            self.client.get("/api/notifications", headers=headers(user="2")).json()[
                "data"
            ]["unread_count"]
            == 1
        )

    def test_concurrent_reports_are_deduplicated_without_losing_occurrences(
        self, monkeypatch
    ):
        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")

        def report(_):
            return publish(
                workspace="notification-concurrent",
                type="operation.failed",
                title="Operation failed",
                message="Retry the operation.",
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            identifiers = list(pool.map(report, range(8)))
        assert len(set(identifiers)) == 1
        data = self.client.get(
            "/api/notifications", headers=headers("notification-concurrent")
        ).json()["data"]
        assert data["items"][0]["occurrences"] == 8

    def test_authentication_and_generic_review_failures_are_actionable(
        self, monkeypatch
    ):
        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")
        report_failure(
            AuthenticationError(
                "test-placeholder", "deepseek", "deepseek/deepseek-chat"
            ),
            workspace="notification-other",
            model="deepseek/deepseek-chat",
        )
        report_failure(
            RuntimeError("private repository diagnostic"),
            workspace="notification-other",
            repository="owner/repo",
        )
        response = self.client.get(
            "/api/notifications", headers=headers("notification-other")
        )
        assert {item["type"] for item in response.json()["data"]["items"]} == {
            "model.authentication_failed",
            "review.failed",
        }
        assert "private repository diagnostic" not in response.text

    def test_notification_storage_failure_does_not_replace_the_provider_error(
        self, monkeypatch
    ):
        def unavailable(**kwargs):
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr("src.core.notifications.publish", unavailable)
        report_failure(
            RuntimeError("Insufficient Balance"), workspace="notification-down"
        )

    def test_unauthenticated_reads_are_refused(self):
        assert self.client.get("/api/notifications").status_code == 422

    def test_pagination_keeps_the_unread_count_for_the_whole_workspace(
        self, monkeypatch
    ):
        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")
        for source in ("first", "second", "third"):
            publish(
                workspace="notification-pages",
                type="operation.failed",
                source=source,
                title="Work interrupted",
                message="Retry the operation.",
            )
        first = self.client.get(
            "/api/notifications?limit=2", headers=headers("notification-pages")
        ).json()["data"]
        second = self.client.get(
            "/api/notifications?limit=2&offset=2", headers=headers("notification-pages")
        ).json()["data"]
        assert first["has_more"] is True
        assert second["has_more"] is False
        assert first["unread_count"] == second["unread_count"] == 3
        assert len(first["items"]) == 2
        assert len(second["items"]) == 1

    def test_email_outbox_requires_gateway_credentials_and_current_lease(
        self, monkeypatch
    ):
        monkeypatch.setenv("JWT_SECRET", "notifications-test-signing-placeholder")
        identifier = publish(
            workspace="notification-email",
            type="operation.failed",
            title="Work interrupted",
            message="Retry the operation.",
        )
        service = {
            "Authorization": "Bearer "
            + jwt.encode(
                {
                    "sub": "gateway",
                    "aud": "sourceant:notifications",
                    "exp": int(time.time()) + 300,
                },
                os.environ["JWT_SECRET"],
                algorithm="HS256",
            )
        }
        prefix = "/api/notifications/email"
        assert (
            self.client.get(prefix + "/pending", headers=headers()).status_code == 401
        )
        assert (
            identifier
            in self.client.get(prefix + "/pending", headers=service).json()["data"]
        )
        claim = self.client.post(prefix + f"/{identifier}/claim", headers=service)
        assert claim.status_code == 200
        lease = claim.json()["data"]["lease"]
        assert claim.json()["data"]["workspace"] == "notification-email"
        assert (
            self.client.post(
                prefix + f"/{identifier}/claim", headers=service
            ).status_code
            == 409
        )
        assert (
            self.client.post(
                prefix + f"/{identifier}/sent",
                json={"lease": "wrong-placeholder"},
                headers=service,
            ).status_code
            == 409
        )
        self.client.post(
            prefix + f"/{identifier}/release", json={"lease": lease}, headers=service
        ).raise_for_status()
        second = self.client.post(
            prefix + f"/{identifier}/claim", headers=service
        ).json()["data"]["lease"]
        assert second != lease
        assert (
            self.client.post(
                prefix + f"/{identifier}/sent", json={"lease": lease}, headers=service
            ).status_code
            == 409
        )
        self.client.post(
            prefix + f"/{identifier}/sent", json={"lease": second}, headers=service
        ).raise_for_status()
        assert (
            identifier
            not in self.client.get(prefix + "/pending", headers=service).json()["data"]
        )
        assert (
            self.client.get(
                "/api/notifications", headers=headers("notification-email")
            ).json()["data"]["unread_count"]
            == 1
        )
