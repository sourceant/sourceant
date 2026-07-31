import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from src.auth import get_current_user
from src.config.db import get_session
from src.core.responses import success_response
from src.models.review_record import ReviewRecord
from src.utils.concurrency import gather_bounded
from src.utils.pagination import Params, as_data, page_of, page_of_query
from src.utils.provider_pages import fetch_all
from src.utils.review_cache import get_review as get_cached_review
from src.utils.review_cache import save_review as save_cached_review

router = APIRouter()

_GITHUB_API = "https://api.github.com"


class RerunRequest(BaseModel):
    repo: str
    number: int
    # Preview by default: generate the review and return it without posting.
    post: bool = False
    # Ignore a review already generated for this revision and pay for a new one.
    refresh: bool = False


@router.post("/rerun")
async def rerun_review(
    data: RerunRequest,
    user: dict = Depends(get_current_user),
):
    """Re-run the reviewer for a pull request; preview to the dashboard or post to GitHub."""
    from src.core.plugins.plugin_registry import plugin_registry
    from src.models.pull_request import PullRequest
    from src.models.repository import Repository

    github_token = user.get("github_token")
    if not github_token:
        raise HTTPException(status_code=400, detail="No GitHub token available")

    owner, _, name = data.repo.partition("/")
    if not owner or not name:
        raise HTTPException(status_code=400, detail="Invalid repository name")

    async with httpx.AsyncClient(timeout=15) as client:
        pr_resp = await client.get(
            f"{_GITHUB_API}/repos/{data.repo}/pulls/{data.number}",
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if pr_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not load the pull request")
    pr = pr_resp.json()

    head_sha = (pr.get("head") or {}).get("sha")
    if not data.post and not data.refresh:
        cached = get_cached_review(data.repo, data.number, head_sha)
        if cached:
            return success_response({**cached, "cached": True})

    plugin = plugin_registry.get_plugin("code_reviewer")
    if plugin is None:
        raise HTTPException(status_code=503, detail="Reviewer is not available")

    repository = Repository(name=name, owner=owner)
    pull_request = PullRequest(
        number=pr["number"],
        title=pr.get("title"),
        draft=pr.get("draft", False),
        merged=pr.get("merged", False),
        base_sha=(pr.get("base") or {}).get("sha"),
        head_sha=(pr.get("head") or {}).get("sha"),
    )
    pr_metadata = {
        "title": pr.get("title"),
        "description": pr.get("body"),
        "number": pr["number"],
        "base_ref": (pr.get("base") or {}).get("ref"),
        "head_ref": (pr.get("head") or {}).get("ref"),
    }

    result = await plugin.generate_review(
        repository,
        pull_request,
        pr_metadata=pr_metadata,
        event_type=None,
        repository_full_name=data.repo,
        post=data.post,
    )
    if result.get("status") == "success":
        save_cached_review(data.repo, data.number, head_sha, result)
    return success_response({**result, "cached": False})


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


@router.get("/pulls")
async def list_pulls(
    repo: list[str] = Query(
        ..., description="Repository full name, e.g. owner/name. May repeat."
    ),
    params: Params = Depends(),
    user: dict = Depends(get_current_user),
):
    """One page of open pull requests across the given repositories."""
    github_token = user.get("github_token")
    if not github_token:
        return success_response(page_of([], params))

    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
    }

    async def _pulls_of(client: httpx.AsyncClient, full_name: str):
        pulls, truncated = await fetch_all(
            client,
            f"{_GITHUB_API}/repos/{full_name}/pulls",
            headers,
            params={"state": "open", "sort": "updated"},
        )
        return full_name, pulls, truncated

    async with httpx.AsyncClient(timeout=30) as client:
        gathered = await gather_bounded(
            [
                lambda c=client, name=name: _pulls_of(c, name)
                for name in dict.fromkeys(repo)
            ]
        )

    if all(truncated and not pulls for _, pulls, truncated in gathered):
        raise HTTPException(status_code=502, detail="GitHub API error")

    results = [
        {
            "repo": full_name,
            "number": pr["number"],
            "title": pr["title"],
            "state": pr["state"],
            "draft": pr.get("draft", False),
            "author": (pr.get("user") or {}).get("login"),
            "url": pr.get("html_url"),
            "updated_at": pr.get("updated_at"),
        }
        for full_name, pulls, _ in gathered
        for pr in pulls
    ]
    results.sort(key=lambda item: item["updated_at"] or "", reverse=True)

    return success_response(page_of(results, params))


@router.get("/detail")
async def review_detail(
    repo: str = Query(..., description="Repository full name, e.g. owner/name"),
    number: int = Query(..., description="Pull request number"),
    user: dict = Depends(get_current_user),
):
    """A pull request with the reviews posted on it, from live GitHub."""
    github_token = user.get("github_token")
    if not github_token:
        raise HTTPException(status_code=400, detail="No GitHub token available")

    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        pr_resp = await client.get(
            f"{_GITHUB_API}/repos/{repo}/pulls/{number}", headers=headers
        )
        if pr_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="GitHub API error")
        pr = pr_resp.json()

        reviews_resp, comments_resp, files_resp = await asyncio.gather(
            client.get(
                f"{_GITHUB_API}/repos/{repo}/pulls/{number}/reviews",
                headers=headers,
                params={"per_page": 50},
            ),
            client.get(
                f"{_GITHUB_API}/repos/{repo}/pulls/{number}/comments",
                headers=headers,
                params={"per_page": 100},
            ),
            client.get(
                f"{_GITHUB_API}/repos/{repo}/pulls/{number}/files",
                headers=headers,
                params={"per_page": 100},
            ),
        )
        reviews = reviews_resp.json() if reviews_resp.status_code == 200 else []
        comments = comments_resp.json() if comments_resp.status_code == 200 else []
        files = files_resp.json() if files_resp.status_code == 200 else []

    return success_response(
        {
            "number": pr["number"],
            "title": pr["title"],
            "body": pr.get("body") or "",
            "state": pr["state"],
            "author": (pr.get("user") or {}).get("login"),
            "url": pr.get("html_url"),
            "head_sha": (pr.get("head") or {}).get("sha"),
            "base_sha": (pr.get("base") or {}).get("sha"),
            "reviews": [
                {
                    "author": (r.get("user") or {}).get("login"),
                    "state": r.get("state"),
                    "body": r.get("body") or "",
                    "submitted_at": r.get("submitted_at"),
                }
                for r in reviews
                if r.get("body")
            ],
            # The findings themselves are inline comments, not the review body,
            # which carries only a pointer to the overview comment.
            "comments": [
                {
                    "id": c.get("id"),
                    "author": (c.get("user") or {}).get("login"),
                    "body": c.get("body") or "",
                    "path": c.get("path"),
                    "line": c.get("line") or c.get("original_line"),
                    "start_line": c.get("start_line") or c.get("original_start_line"),
                    "side": c.get("side"),
                    "diff_hunk": c.get("diff_hunk"),
                    "in_reply_to_id": c.get("in_reply_to_id"),
                    "url": c.get("html_url"),
                    "created_at": c.get("created_at"),
                }
                for c in comments
                if c.get("body")
            ],
            # What the change touches at all, not only what the review remarked
            # on. A file nobody expected to see is a finding in itself.
            "files": [
                {
                    "filename": f.get("filename"),
                    "previous_filename": f.get("previous_filename"),
                    "status": f.get("status"),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "changes": f.get("changes", 0),
                    "patch": f.get("patch"),
                }
                for f in files
            ],
        }
    )


@router.get("")
async def list_reviews(
    repo: list[str] = Query(
        ..., description="Repository full name, e.g. owner/name. May repeat."
    ),
    params: Params = Depends(),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """One page of SourceAnt's reviews, enriched with live GitHub state."""
    github_token = user.get("github_token")

    page = page_of_query(
        session,
        select(ReviewRecord)
        .where(ReviewRecord.repository_full_name.in_(repo))
        .order_by(ReviewRecord.id.desc()),
        params,
    )

    # Only the page is enriched, so the number of provider calls follows the
    # page size rather than the whole review history.
    wanted = {(record.repository_full_name, record.pr_number) for record in page.items}

    async with httpx.AsyncClient(timeout=10) as client:

        async def _fetch(full_name: str, number: int):
            return (full_name, number), await _fetch_pull_request(
                client, full_name, number, github_token
            )

        pr_cache = dict(
            await gather_bounded(
                [
                    lambda name=name, number=number: _fetch(name, number)
                    for name, number in wanted
                ]
            )
        )

    results = []
    for record in page.items:
        pr = pr_cache.get((record.repository_full_name, record.pr_number))
        results.append(
            {
                "id": record.id,
                "repository_full_name": record.repository_full_name,
                "repo": record.repository_full_name,
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

    return success_response(as_data(page, results))
