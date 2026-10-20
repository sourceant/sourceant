import copy
import json
from pathlib import Path

import httpx
import jwt
import pytest

from src.api.routes import requirements, work
from src.core.work.github import GitHubTracker
from src.tests.test_dashboard_backend_api import api as api

FIXTURES = Path(__file__).parent / "fixtures/work"
REPO = "margince/margince"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeGitHub:
    """GitHub answering from captured responses, remembering every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.labels = {"status: in progress", "priority: normal", "priority: high"}
        self.missing_issues: set[int] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == f"/repos/{REPO}/issues" and request.method == "GET":
            return httpx.Response(200, json=_load("labeled-issues.json"))
        if path == "/repos/sourceant/sourceant/issues":
            return httpx.Response(200, json=_load("issues.json"))
        if path == f"/repos/{REPO}/assignees":
            return httpx.Response(200, json=_load("assignees.json"))
        if path.startswith(f"/repos/{REPO}/labels/"):
            name = request.url.path.split("/labels/", 1)[1]
            name = httpx.URL("http://x/" + name).path[1:]
            return httpx.Response(200 if name in self.labels else 404, json={})
        if path == f"/repos/{REPO}/labels" and request.method == "POST":
            self.labels.add(json.loads(request.content)["name"])
            return httpx.Response(201, json={})
        if path.startswith(f"/repos/{REPO}/issues/") and path.endswith("/comments"):
            return httpx.Response(201, json=_load("comments.json")[0])
        if path.startswith(f"/repos/{REPO}/issues/"):
            number = int(path.rsplit("/", 1)[1])
            if number in self.missing_issues:
                return httpx.Response(404, json={"message": "Not Found"})
            issue = copy.deepcopy(_load("issue.json"))
            issue["number"] = number
            if request.method == "PATCH":
                patch = json.loads(request.content)
                if "labels" in patch:
                    issue["labels"] = [{"name": name} for name in patch["labels"]]
                if "state" in patch:
                    issue["state"] = patch["state"]
                if "assignees" in patch:
                    issue["assignees"] = [{"login": a} for a in patch["assignees"]]
            return httpx.Response(200, json=issue)
        if path == f"/repos/{REPO}/issues" and request.method == "POST":
            sent = json.loads(request.content)
            issue = copy.deepcopy(_load("issue.json"))
            issue.update(title=sent["title"], body=sent["body"], number=9001)
            issue["labels"] = [{"name": name} for name in sent["labels"]]
            return httpx.Response(201, json=issue)
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def github(api, monkeypatch):
    client, headers, _, _ = api
    client.app.include_router(work.router, prefix="/api/work")
    monkeypatch.setattr(
        requirements, "connected_names", lambda user: [REPO, "sourceant/sourceant"]
    )
    fake = FakeGitHub()
    monkeypatch.setattr(
        work,
        "tracker_for",
        lambda user: GitHubTracker(
            user["github_token"],
            client=lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        ),
    )

    def signed_in():
        credentials = headers()
        claims = jwt.decode(
            credentials["Authorization"][7:],
            "dashboard-backend-test-secret",
            algorithms=["HS256"],
        )
        claims["github_token"] = "test-placeholder"
        return {
            "Authorization": "Bearer "
            + jwt.encode(claims, "dashboard-backend-test-secret", algorithm="HS256")
        }

    return client, signed_in, fake


def test_work_reads_status_priority_and_owner_from_the_labels_teams_use(github):
    client, headers, _ = github

    response = client.get("/api/work", headers=headers(), params={"repo": REPO})

    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    first = next(item for item in items if item["number"] == 8158)
    assert first["status"] == "in_progress"
    assert first["priority"] == "normal"
    assert first["assignees"] == ["tinnhlt-gra"]
    assert first["kind"] == "issue"
    assert {item["priority"] for item in items} >= {"normal", "high"}


def test_merged_pull_requests_are_done_and_hidden_unless_asked_for(github):
    client, headers, _ = github
    query = {"repo": "sourceant/sourceant"}

    hidden = client.get("/api/work", headers=headers(), params=query)
    shown = client.get(
        "/api/work", headers=headers(), params={**query, "include_done": True}
    )

    assert hidden.json()["data"]["items"] == []
    done = shown.json()["data"]["items"]
    assert done and all(i["kind"] == "pull" and i["status"] == "done" for i in done)


def test_work_filters_by_priority_and_owner(github):
    client, headers, _ = github

    response = client.get(
        "/api/work",
        headers=headers(),
        params={"repo": REPO, "priority": "high", "assignee": "LarsGradion"},
    )

    numbers = [item["number"] for item in response.json()["data"]["items"]]
    assert numbers and 7977 in numbers and 8158 not in numbers


def test_moving_work_swaps_its_status_label_and_makes_the_label_if_missing(github):
    client, headers, fake = github

    response = client.patch(
        "/api/work/item",
        headers=headers(),
        json={"repo": REPO, "number": 8158, "status": "in_review", "assignees": ["a"]},
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"]
    assert item["status"] == "in_review"
    assert item["assignees"] == ["a"]
    assert "status: in progress" not in item["labels"]
    created = [
        r
        for r in fake.requests
        if r.method == "POST" and r.url.path.endswith("/labels")
    ]
    assert [json.loads(r.content)["name"] for r in created] == ["status: in review"]
    patch = next(r for r in fake.requests if r.method == "PATCH")
    assert json.loads(patch.content)["state"] == "open"


def test_done_closes_the_issue(github):
    client, headers, fake = github

    response = client.patch(
        "/api/work/item",
        headers=headers(),
        json={"repo": REPO, "number": 8158, "status": "done"},
    )

    assert response.json()["data"]["status"] == "done"
    patch = json.loads(next(r for r in fake.requests if r.method == "PATCH").content)
    assert patch["state"] == "closed"
    assert not any(label.startswith("status: ") for label in patch["labels"])


def test_a_bulk_change_reports_each_item_it_could_not_change(github):
    client, headers, fake = github
    fake.missing_issues.add(404)

    response = client.post(
        "/api/work/bulk",
        headers=headers(),
        json={
            "items": [{"repo": REPO, "number": 8158}, {"repo": REPO, "number": 404}],
            "change": {"priority": "urgent"},
        },
    )

    data = response.json()["data"]
    assert [i["priority"] for i in data["changed"]] == ["urgent"]
    assert [f["number"] for f in data["failed"]] == [404]


def test_a_finding_is_tracked_as_an_issue_that_links_to_its_code(github):
    client, headers, fake = github

    response = client.post(
        "/api/work/track",
        headers=headers(),
        json={
            "repo": REPO,
            "title": "Cycles never end in root",
            "detail": "The loop never ends on a cycle.",
            "path": "src/core/topology/suggestions.py",
            "line": 11,
            "revision": "abc123",
            "pull": 42,
            "priority": "high",
        },
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"]
    assert item["kind"] == "finding"
    assert item["priority"] == "high"
    sent = json.loads(
        next(
            r
            for r in fake.requests
            if r.method == "POST" and r.url.path.endswith("/issues")
        ).content
    )
    assert (
        "https://github.com/margince/margince/blob/abc123/src/core/topology/suggestions.py#L11"
        in sent["body"]
    )
    assert "#42" in sent["body"]
    assert sent["labels"] == ["sourceant: finding", "priority: high"]


def test_people_and_comments_go_to_the_tracker(github):
    client, headers, fake = github

    people = client.get("/api/work/people", headers=headers(), params={"repo": REPO})
    said = client.post(
        "/api/work/comment",
        headers=headers(),
        json={"repo": REPO, "number": 8158, "body": "Taking this."},
    )

    assert len(people.json()["data"]) == len(_load("assignees.json"))
    assert said.status_code == 200
    comment = next(r for r in fake.requests if r.url.path.endswith("/8158/comments"))
    assert json.loads(comment.content) == {"body": "Taking this."}


def test_work_outside_the_workspace_is_refused(github):
    client, headers, fake = github

    response = client.get(
        "/api/work", headers=headers(), params={"repo": "someone/else"}
    )

    assert response.status_code == 403
    assert fake.requests == []


def test_each_item_carries_the_state_github_colours_it_by():
    from src.core.work.github import item_of

    seen = {
        issue["number"]: item_of("r/r", issue).state
        for issue in _load("states.json")
        + _load("issues.json")[:1]
        + _load("labeled-issues.json")[:1]
    }

    assert seen == {
        191: "completed",
        177: "closed",
        200: "draft",
        15928: "not_planned",
        209: "merged",
        8158: "open",
    }
