import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from src.auth import get_current_user
from src.core.responses import success_response

router = APIRouter()

_GITHUB_API = "https://api.github.com"


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
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
            params={"state": "open", "per_page": 50, "sort": "updated"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub API error")

    results = []
    for issue in resp.json():
        # GitHub returns pull requests through the issues endpoint too; exclude them.
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
