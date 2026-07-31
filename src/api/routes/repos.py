from datetime import datetime, timezone
from typing import Optional

import httpx

from src.utils.provider_pages import fetch_all
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from src.auth import get_current_user
from src.config.db import get_session
from src.core.responses import success_response
from src.models.repository import Repository
from src.models.connected_repository import ConnectedRepository
from src.utils.pagination import Params, as_data, page_of, page_of_query

router = APIRouter()


class ConnectRepoRequest(BaseModel):
    github_id: int
    full_name: str
    name: str
    description: Optional[str] = None
    private: bool = False
    language: Optional[str] = None
    default_branch: str = "main"
    visibility: str = "public"
    archived: bool = False
    owner: str
    owner_type: str = "User"
    url: str


@router.get("")
async def list_repos(
    q: str = Query("", description="Match against the name or description"),
    params: Params = Depends(),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """One page of the repos the user's GitHub token reaches, with connected status."""
    github_token = user.get("github_token")
    if not github_token:
        raise HTTPException(status_code=401, detail="No GitHub token available")

    async with httpx.AsyncClient(timeout=30) as client:
        github_repos, truncated = await fetch_all(
            client,
            "https://api.github.com/user/repos",
            {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
            params={"sort": "updated"},
        )
    if not github_repos and truncated:
        raise HTTPException(status_code=502, detail="GitHub API error")

    user_id = user["user_id"]
    connected_rows = session.exec(
        select(ConnectedRepository).where(ConnectedRepository.user_id == user_id)
    ).all()
    connected_ids = {row.repository_id for row in connected_rows}

    all_repos = session.exec(select(Repository)).all()
    repo_map = {r.full_name: r.id for r in all_repos}

    needle = q.strip().lower()
    results = []
    for gh_repo in github_repos:
        if needle and not _matches(gh_repo, needle):
            continue
        repo_id = repo_map.get(gh_repo["full_name"])
        results.append(
            {
                **gh_repo,
                "repo_id": repo_id,
                "connected": repo_id in connected_ids if repo_id else False,
            }
        )

    # The whole list is read, and searched, before the page is cut: connected
    # status comes from here rather than from the provider, and a search that
    # only covered the page in hand would miss most of what it was asked about.
    return success_response(page_of(results, params))


def _matches(repo: dict, needle: str) -> bool:
    haystack = f"{repo.get('full_name') or ''} {repo.get('description') or ''}"
    return needle in haystack.lower()


@router.get("/connected")
async def list_connected_repos(
    params: Params = Depends(),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """One page of the user's connected repositories, from the DB cache."""
    user_id = user["user_id"]
    connected_rows = session.exec(
        select(ConnectedRepository).where(ConnectedRepository.user_id == user_id)
    ).all()

    if not connected_rows:
        return success_response(page_of([], params))

    repo_ids = [row.repository_id for row in connected_rows]
    connected_at_map = {row.repository_id: row.connected_at for row in connected_rows}

    page = page_of_query(
        session,
        select(Repository)
        .where(Repository.id.in_(repo_ids))
        .order_by(Repository.full_name),
        params,
    )

    return success_response(
        as_data(
            page,
            [
                {
                    "id": repo.id,
                    "name": repo.full_name,
                    "full_name": repo.full_name,
                    "description": repo.description,
                    "private": repo.private,
                    "language": repo.language,
                    "default_branch": repo.default_branch,
                    "visibility": repo.visibility,
                    "archived": repo.archived,
                    "owner": repo.owner,
                    "url": repo.url,
                    "contexts": 0,
                    "connected_at": connected_at_map[repo.id].isoformat(),
                    "status": "active",
                }
                for repo in page.items
            ],
        )
    )


@router.post("/connect")
async def connect_repo(
    data: ConnectRepoRequest,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Connect a GitHub repository for the current user."""
    user_id = user["user_id"]

    repo = session.exec(
        select(Repository).where(Repository.full_name == data.full_name)
    ).first()

    if not repo:
        repo = Repository(
            provider="github",
            name=data.name,
            full_name=data.full_name,
            url=data.url,
            description=data.description,
            private=data.private,
            archived=data.archived,
            visibility=data.visibility,
            owner=data.owner,
            owner_type=data.owner_type,
            language=data.language,
            default_branch=data.default_branch,
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)

    existing = session.exec(
        select(ConnectedRepository).where(
            ConnectedRepository.user_id == user_id,
            ConnectedRepository.repository_id == repo.id,
        )
    ).first()

    if existing:
        return success_response(
            data={"id": repo.id}, message="Repository already connected"
        )

    connection = ConnectedRepository(
        user_id=user_id,
        repository_id=repo.id,
    )
    session.add(connection)
    session.commit()

    _sync_repository(session, data)

    return success_response(
        data={"id": repo.id}, message="Repository connected", status_code=201
    )


@router.delete("/{repo_id}/disconnect")
async def disconnect_repo(
    repo_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Disconnect a repository for the current user."""
    user_id = user["user_id"]

    connection = session.exec(
        select(ConnectedRepository).where(
            ConnectedRepository.user_id == user_id,
            ConnectedRepository.repository_id == repo_id,
        )
    ).first()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    session.delete(connection)
    session.commit()

    return success_response(data=None, message="Repository disconnected")


def _sync_repository(session: Session, data: ConnectRepoRequest) -> None:
    """Sync repository metadata from the connect request."""
    repo = session.exec(
        select(Repository).where(Repository.full_name == data.full_name)
    ).first()

    if not repo:
        return

    repo.description = data.description
    repo.private = data.private
    repo.language = data.language
    repo.default_branch = data.default_branch
    repo.visibility = data.visibility
    repo.archived = data.archived
    repo.updated_at = datetime.now(timezone.utc)
    session.add(repo)
    session.commit()
