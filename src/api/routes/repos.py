from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from src.auth import get_current_user
from src.config.db import get_session
from src.core.responses import success_response
from src.models.repository import Repository
from src.models.connected_repository import ConnectedRepository

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
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """List repos the user has access to via their GitHub token, with connected status."""
    github_token = user.get("github_token")
    if not github_token:
        raise HTTPException(status_code=400, detail="No GitHub token available")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
            params={"per_page": 100, "sort": "updated"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="GitHub API error")
        github_repos = resp.json()

    user_id = user["user_id"]
    connected_rows = session.exec(
        select(ConnectedRepository).where(ConnectedRepository.user_id == user_id)
    ).all()
    connected_ids = {row.repository_id for row in connected_rows}

    all_repos = session.exec(select(Repository)).all()
    repo_map = {r.full_name: r.id for r in all_repos}

    results = []
    for gh_repo in github_repos:
        repo_id = repo_map.get(gh_repo["full_name"])
        results.append(
            {
                **gh_repo,
                "repo_id": repo_id,
                "connected": repo_id in connected_ids if repo_id else False,
            }
        )

    return results


@router.get("/connected")
async def list_connected_repos(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """List only the user's connected repositories from the DB cache."""
    user_id = user["user_id"]
    connected_rows = session.exec(
        select(ConnectedRepository).where(ConnectedRepository.user_id == user_id)
    ).all()

    if not connected_rows:
        return []

    repo_ids = [row.repository_id for row in connected_rows]
    connected_at_map = {row.repository_id: row.connected_at for row in connected_rows}

    repos = session.exec(select(Repository).where(Repository.id.in_(repo_ids))).all()

    return [
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
        for repo in repos
    ]


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
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
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
