import httpx
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from src.auth import get_current_user
from src.config.db import get_session
from src.core.responses import success_response
from src.models.review_record import ReviewRecord

router = APIRouter()

_GITHUB_API = "https://api.github.com"


async def _fetch_pull_request(
    client: httpx.AsyncClient, repo: str, number: int, token: str | None
) -> dict | None:
    if not token:
        return None
    try:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{repo}/pulls/{number}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
        )
    except httpx.HTTPError:
        return None
    return resp.json() if resp.status_code == 200 else None


@router.get("")
async def list_reviews(
    repo: str = Query(..., description="Repository full name, e.g. owner/name"),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """List SourceAnt's reviews for a repository, enriched with live GitHub PR state."""
    github_token = user.get("github_token")

    records = session.exec(
        select(ReviewRecord)
        .where(ReviewRecord.repository_full_name == repo)
        .order_by(ReviewRecord.id.desc())
    ).all()

    pr_cache: dict[int, dict | None] = {}
    results = []
    async with httpx.AsyncClient(timeout=10) as client:
        for record in records:
            if record.pr_number not in pr_cache:
                pr_cache[record.pr_number] = await _fetch_pull_request(
                    client, repo, record.pr_number, github_token
                )
            pr = pr_cache[record.pr_number]
            results.append(
                {
                    "id": record.id,
                    "repository_full_name": record.repository_full_name,
                    "pr_number": record.pr_number,
                    "status": record.status,
                    "reviewed_head_sha": record.reviewed_head_sha,
                    "title": pr.get("title") if pr else None,
                    "state": pr.get("state") if pr else None,
                    "author": (pr.get("user") or {}).get("login") if pr else None,
                    "url": pr.get("html_url") if pr else None,
                    "updated_at": pr.get("updated_at") if pr else None,
                }
            )

    return success_response(results)
