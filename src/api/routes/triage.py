import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.auth import get_current_user
from src.core.responses import success_response

router = APIRouter()

_GITHUB_API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }


class TriageAction(BaseModel):
    repo: str
    number: int
    action: str  # comment | label | close
    comment: str | None = None
    labels: list[str] | None = None


@router.get("")
async def list_triage(
    repo: str = Query(..., description="Repository full name, e.g. owner/name"),
    user: dict = Depends(get_current_user),
):
    """The open-issue triage queue for a repository, from live GitHub state."""
    github_token = user.get("github_token")
    if not github_token:
        return success_response([])

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{repo}/issues",
            headers=_headers(github_token),
            params={"state": "open", "per_page": 50, "sort": "updated"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub API error")

    results = []
    for issue in resp.json():
        if "pull_request" in issue:
            continue
        results.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "state": issue["state"],
                "author": (issue.get("user") or {}).get("login"),
                "labels": [label["name"] for label in issue.get("labels", [])],
                "comments": issue.get("comments", 0),
                "url": issue.get("html_url"),
                "updated_at": issue.get("updated_at"),
            }
        )

    return success_response(results)


@router.get("/detail")
async def triage_detail(
    repo: str = Query(...),
    number: int = Query(...),
    user: dict = Depends(get_current_user),
):
    """A single issue with its body and comments, from live GitHub."""
    github_token = user.get("github_token")
    if not github_token:
        raise HTTPException(status_code=400, detail="No GitHub token available")

    async with httpx.AsyncClient(timeout=10) as client:
        issue_resp = await client.get(
            f"{_GITHUB_API}/repos/{repo}/issues/{number}",
            headers=_headers(github_token),
        )
        if issue_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="GitHub API error")
        issue = issue_resp.json()

        comments_resp = await client.get(
            f"{_GITHUB_API}/repos/{repo}/issues/{number}/comments",
            headers=_headers(github_token),
            params={"per_page": 50},
        )
        comments = comments_resp.json() if comments_resp.status_code == 200 else []

    return success_response(
        {
            "number": issue["number"],
            "title": issue["title"],
            "body": issue.get("body") or "",
            "state": issue["state"],
            "author": (issue.get("user") or {}).get("login"),
            "labels": [label["name"] for label in issue.get("labels", [])],
            "url": issue.get("html_url"),
            "comments": [
                {
                    "author": (c.get("user") or {}).get("login"),
                    "body": c.get("body") or "",
                    "created_at": c.get("created_at"),
                }
                for c in comments
            ],
        }
    )


@router.post("/action")
async def triage_action(
    data: TriageAction,
    user: dict = Depends(get_current_user),
):
    """Take an action on an issue: comment, add labels, or close."""
    github_token = user.get("github_token")
    if not github_token:
        raise HTTPException(status_code=400, detail="No GitHub token available")

    base = f"{_GITHUB_API}/repos/{data.repo}/issues/{data.number}"
    async with httpx.AsyncClient(timeout=10) as client:
        if data.action == "comment":
            if not data.comment:
                raise HTTPException(status_code=400, detail="Comment body is required")
            resp = await client.post(
                f"{base}/comments",
                headers=_headers(github_token),
                json={"body": data.comment},
            )
        elif data.action == "label":
            if not data.labels:
                raise HTTPException(status_code=400, detail="At least one label is required")
            resp = await client.post(
                f"{base}/labels",
                headers=_headers(github_token),
                json={"labels": data.labels},
            )
        elif data.action == "close":
            resp = await client.patch(
                base,
                headers=_headers(github_token),
                json={"state": "closed"},
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {data.action}")

    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail="GitHub rejected the action")

    return success_response({"ok": True})
