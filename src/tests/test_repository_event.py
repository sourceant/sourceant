import pytest
from unittest.mock import patch
from src.models.repository_event import RepositoryEvent
from src.tests.base_test import BaseTestCase


class TestRepositoryEvents(BaseTestCase):
    @pytest.mark.parametrize("stateless_mode", [True, False])
    def test_github_webhook(self, stateless_mode):
        with patch("src.config.db.STATELESS_MODE", stateless_mode), patch(
            "src.controllers.repository_event_controller.STATELESS_MODE", stateless_mode
        ):
            payload = {
                "action": "opened",
                "pull_request": {
                    "url": "https://api.github.com/repos/sourceant/sourceant/pulls/1",
                    "title": "Fix bug",
                    "number": 1,
                },
                "repository": {"full_name": "sourceant/sourceant"},
                "sender": {"login": "octocat"},
            }

            if stateless_mode:
                with patch(
                    "src.models.repository_event.RepositoryEvent.save"
                ) as mock_save:
                    response = self.client.post(
                        "/api/prs/github-webhook",
                        headers={"X-GitHub-Event": "pull_request"},
                        json=payload,
                    )
                    assert response.status_code == 201
                    mock_save.assert_not_called()
            else:
                response = self.client.post(
                    "/api/prs/github-webhook",
                    headers={"X-GitHub-Event": "pull_request"},
                    json=payload,
                )
                assert response.status_code == 201

                # Verify data was saved to the database
                repo_events_from_db = RepositoryEvent.get_all()
                assert any(
                    event.number == 1
                    and event.repository_full_name == "sourceant/sourceant"
                    for event in repo_events_from_db
                )

            # Common assertions for both modes
            data = response.json()
            assert data["status"] == "success"
            assert data["data"]["repository_full_name"] == "sourceant/sourceant"
            assert data["data"]["action"] == "opened"

    @pytest.mark.parametrize("stateless_mode", [True, False])
    def test_get_repository_events(self, stateless_mode):
        with patch("src.config.db.STATELESS_MODE", stateless_mode), patch(
            "src.controllers.repository_event_controller.STATELESS_MODE", stateless_mode
        ):
            if not stateless_mode:
                RepositoryEvent.create(
                    type="pull_request",
                    action="opened",
                    repository_full_name="sourceant/sourceant",
                    title="Add new feature",
                    url="https://api.github.com/repos/sourceant/sourceant/pulls/1",
                    number=1,
                    payload={},
                )

            response = self.client.get("/repository-events")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert isinstance(data["data"], list)

            if stateless_mode:
                assert data["data"] == []
            else:
                assert any(
                    event["repository_full_name"] == "sourceant/sourceant"
                    for event in data["data"]
                )

    def test_invalid_signature_returns_400(self):
        with patch("src.api.routes.pr.GITHUB_SECRET", "real-secret"):
            payload = {
                "action": "opened",
                "pull_request": {
                    "url": "https://api.github.com/repos/sourceant/sourceant/pulls/1",
                    "title": "Fix bug",
                    "number": 1,
                },
                "repository": {"full_name": "sourceant/sourceant"},
                "sender": {"login": "octocat"},
            }

            response = self.client.post(
                "/api/prs/github-webhook",
                headers={
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": "sha256=invalidsignature",
                },
                json=payload,
            )
            assert response.status_code == 400
            assert response.json()["detail"] == "Invalid GitHub signature"

    def test_missing_required_fields_returns_422(self):
        payload = {
            "action": "opened",
        }

        response = self.client.post(
            "/api/prs/github-webhook",
            headers={"X-GitHub-Event": "pull_request"},
            json=payload,
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "event_type,action",
        [
            ("issues", "opened"),
            ("push", None),
            ("pull_request", "closed"),
            ("pull_request", "review_requested"),
        ],
    )
    def test_all_events_are_created_and_broadcast(self, event_type, action):
        payload = {
            "action": action,
            "pull_request": {
                "url": "https://api.github.com/repos/sourceant/sourceant/pulls/1",
                "title": "Fix bug",
                "number": 1,
            },
            "repository": {"full_name": "sourceant/sourceant"},
            "sender": {"login": "octocat"},
        }

        response = self.client.post(
            "/api/prs/github-webhook",
            headers={"X-GitHub-Event": event_type},
            json=payload,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["type"] == event_type

    def test_get_multiple_repository_events(self):
        with patch("src.config.db.STATELESS_MODE", False), patch(
            "src.controllers.repository_event_controller.STATELESS_MODE", False
        ):
            for i in range(1, 4):
                RepositoryEvent.create(
                    type="pull_request",
                    action="opened",
                    repository_full_name=f"sourceant/repo-{i}",
                    title=f"PR #{i}",
                    url=f"https://api.github.com/repos/sourceant/repo-{i}/pulls/{i}",
                    number=i,
                    payload={},
                )

            response = self.client.get("/repository-events")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert len(data["data"]) >= 3

            repo_names = {event["repository_full_name"] for event in data["data"]}
            for i in range(1, 4):
                assert f"sourceant/repo-{i}" in repo_names
