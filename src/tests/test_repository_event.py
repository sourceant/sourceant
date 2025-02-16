from src.models.repository_event import RepositoryEvent
from src.tests.base_test import BaseTestCase


class TestRepositoryEvents(BaseTestCase):

    def test_github_webhook(self):
        payload = {
            "type": "pull_request",
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
            headers={"X-GitHub-Event": "pull_request"},
            json=payload,
        )

        assert response.status_code == 201
        data = response.json()

        assert data["status"] == "success"
        assert data["data"]["repository"] == "sourceant/sourceant"
        assert data["data"]["action"] == "opened"

        repo_events_from_db = RepositoryEvent.get_all()
        for event in repo_events_from_db:
            if event.number == 1 and event.repository == "sourceant/sourceant":
                assert event.action == "opened"
                assert event.type == "pull_request"
                break

    def test_get_repository_events(self):
        # Ensure the database is populated with at least one event
        RepositoryEvent.create(
            type="pull_request",
            action="opened",
            repository="sourceant/sourceant",
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
        assert any(
            event["repository"] == "sourceant/sourceant" for event in data["data"]
        )
