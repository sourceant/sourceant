"""Drives the paged list endpoints over HTTP, the way the gateway calls them."""

import os
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.api.main import app
from src.config.db import get_session
from src.models.connected_repository import ConnectedRepository
from src.models.repository import Repository
from src.models.review_record import ReviewRecord

TEST_JWT_SECRET = "pagination-api-test-secret"
GITHUB_TOKEN = "gho_test-token"


def _token() -> str:
    return jwt.encode(
        {
            "sub": "1",
            "username": "octocat",
            "github_token": GITHUB_TOKEN,
            "scope": {"workspace_id": "w1", "repository_ids": []},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


def _issue(number: int, repo: str) -> dict:
    """An issue shaped the way GitHub sends one, trimmed to what the route reads."""
    return {
        "number": number,
        "title": f"{repo} issue {number}",
        "state": "open",
        "user": {"login": "octocat"},
        "labels": [{"name": "bug"}],
        "comments": 0,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        # Descending, so the newest issue is the lowest number.
        "updated_at": f"2026-07-{31 - number:02d}T00:00:00Z",
    }


def _pull(number: int, repo: str) -> dict:
    return {
        "number": number,
        "title": f"{repo} pull {number}",
        "state": "open",
        "draft": False,
        "user": {"login": "octocat"},
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "updated_at": f"2026-07-{31 - number:02d}T00:00:00Z",
    }


class TestPagedEndpoints:
    @pytest.fixture(autouse=True)
    def environment(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
        self.headers = {"Authorization": f"Bearer {_token()}"}
        self.requests: list[str] = []

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        self.session = Session(engine)
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app)
        yield
        app.dependency_overrides.pop(get_session, None)
        self.session.close()

    def answer_github(self, monkeypatch, body_for):
        """Answer every provider call from a fixture instead of the network."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            return httpx.Response(200, json=body_for(request))

        real_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            return real_client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    def connect(self, full_names: list[str]) -> None:
        for full_name in full_names:
            owner, _, name = full_name.partition("/")
            repo = Repository(
                provider="github",
                name=name,
                full_name=full_name,
                url=f"https://github.com/{full_name}",
                owner=owner,
                owner_type="Organization",
                private=False,
                archived=False,
                visibility="public",
                default_branch="main",
            )
            self.session.add(repo)
            self.session.commit()
            self.session.refresh(repo)
            self.session.add(
                ConnectedRepository(workspace_id="w1", repository_id=repo.id)
            )
        self.session.commit()

    def triage(self, **params) -> dict:
        return self.client.get(
            "/api/triage", params=params, headers=self.headers
        ).json()["data"]

    def test_a_page_carries_the_totals_with_it(self, monkeypatch):
        self.answer_github(
            monkeypatch, lambda request: [_issue(n, "acme/api") for n in range(1, 26)]
        )

        body = self.triage(repo="acme/api", page=1, size=10)

        assert len(body["items"]) == 10
        assert body["total"] == 25
        assert body["pages"] == 3
        assert body["page"] == 1
        assert body["size"] == 10

    def test_the_second_page_continues_where_the_first_stopped(self, monkeypatch):
        self.answer_github(
            monkeypatch, lambda request: [_issue(n, "acme/api") for n in range(1, 26)]
        )

        first = self.triage(repo="acme/api", page=1, size=10)["items"]
        second = self.triage(repo="acme/api", page=2, size=10)["items"]

        numbers = [item["number"] for item in first + second]
        assert numbers == list(range(1, 21))

    def test_a_scope_of_several_repositories_is_one_merged_page(self, monkeypatch):
        def body_for(request: httpx.Request):
            if "acme/api" in str(request.url):
                return [_issue(n, "acme/api") for n in (1, 3)]
            return [_issue(n, "acme/web") for n in (2, 4)]

        self.answer_github(monkeypatch, body_for)

        body = self.client.get(
            "/api/triage",
            params=[("repo", "acme/api"), ("repo", "acme/web"), ("size", 3)],
            headers=self.headers,
        ).json()["data"]

        assert body["total"] == 4
        # Ordered across both repositories, newest first, rather than one
        # repository's list appended to the other's.
        assert [item["number"] for item in body["items"]] == [1, 2, 3]
        assert [item["repo"] for item in body["items"]] == [
            "acme/api",
            "acme/web",
            "acme/api",
        ]

    def test_pull_requests_are_paged_the_same_way(self, monkeypatch):
        self.answer_github(
            monkeypatch, lambda request: [_pull(n, "acme/api") for n in range(1, 8)]
        )

        body = self.client.get(
            "/api/reviews/pulls",
            params={"repo": "acme/api", "page": 2, "size": 5},
            headers=self.headers,
        ).json()["data"]

        assert body["total"] == 7
        assert len(body["items"]) == 2
        assert body["pages"] == 2

    def test_a_page_beyond_the_last_one_is_empty_rather_than_an_error(
        self, monkeypatch
    ):
        self.answer_github(
            monkeypatch, lambda request: [_issue(n, "acme/api") for n in range(1, 4)]
        )

        response = self.client.get(
            "/api/triage",
            params={"repo": "acme/api", "page": 9, "size": 10},
            headers=self.headers,
        )

        assert response.status_code == 200
        assert response.json()["data"]["items"] == []
        assert response.json()["data"]["total"] == 3

    def test_a_page_larger_than_the_ceiling_is_refused(self, monkeypatch):
        self.answer_github(monkeypatch, lambda request: [])

        response = self.client.get(
            "/api/triage",
            params={"repo": "acme/api", "size": 500},
            headers=self.headers,
        )

        assert response.status_code == 422

    def test_connected_repositories_are_read_a_page_at_a_time(self):
        self.connect([f"acme/repo-{n:02d}" for n in range(1, 8)])

        body = self.client.get(
            "/api/repos/connected",
            params={"page": 2, "size": 5},
            headers=self.headers,
        ).json()["data"]

        assert body["total"] == 7
        assert [item["full_name"] for item in body["items"]] == [
            "acme/repo-06",
            "acme/repo-07",
        ]

    def test_the_repository_search_covers_more_than_the_page_in_hand(self, monkeypatch):
        self.answer_github(
            monkeypatch,
            lambda request: [
                {
                    "id": n,
                    "name": f"repo-{n:02d}",
                    "full_name": f"acme/repo-{n:02d}",
                    "description": "the ledger" if n == 90 else "something else",
                }
                for n in range(1, 101)
            ],
        )

        body = self.client.get(
            "/api/repos",
            params={"q": "ledger", "size": 10},
            headers=self.headers,
        ).json()["data"]

        # The match sits on what would be the ninth page, so a search that only
        # looked at the page in hand would find nothing.
        assert body["total"] == 1
        assert body["items"][0]["full_name"] == "acme/repo-90"

    def test_connected_status_still_answers_for_the_repositories_named(
        self, monkeypatch
    ):
        self.connect(["acme/api"])
        self.answer_github(
            monkeypatch,
            lambda request: [
                {"id": 1, "name": "api", "full_name": "acme/api"},
                {"id": 2, "name": "web", "full_name": "acme/web"},
            ],
        )

        body = self.client.get(
            "/api/repos", params={"size": 10}, headers=self.headers
        ).json()["data"]

        connected = {item["full_name"]: item["connected"] for item in body["items"]}
        assert connected == {"acme/api": True, "acme/web": False}

    def test_only_the_reviews_on_the_page_are_looked_up_upstream(self, monkeypatch):
        for number in range(1, 21):
            self.session.add(
                ReviewRecord(
                    repository_full_name="acme/api",
                    pr_number=number,
                    reviewed_head_sha=f"head{number}",
                    reviewed_base_sha=f"base{number}",
                )
            )
        self.session.commit()
        self.answer_github(
            monkeypatch,
            lambda request: {
                "number": 1,
                "title": "A change",
                "state": "open",
                "user": {"login": "octocat"},
                "html_url": "https://github.com/acme/api/pull/1",
                "updated_at": "2026-07-30T00:00:00Z",
            },
        )

        body = self.client.get(
            "/api/reviews",
            params={"repo": "acme/api", "size": 5},
            headers=self.headers,
        ).json()["data"]

        assert body["total"] == 20
        assert len(body["items"]) == 5
        # The provider is asked about the five reviews on the page, not all
        # twenty in the history.
        assert len(self.requests) == 5
