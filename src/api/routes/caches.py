"""Clearing what was kept because it could be worked out again."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.routes.topology import get_scope
from src.config.db import get_engine
from src.core.cache import CLEARABLE, Owner, cache
from src.core.responses import success_response
from src.core.scope import Scope

router = APIRouter()


class Clearing(BaseModel):
    namespace: str
    scope_type: Optional[str] = None
    scope_id: Optional[str] = None


@router.get("")
def clearable(scope: Scope = Depends(get_scope)):
    return success_response(
        [{"namespace": name, "label": label} for name, label in CLEARABLE.items()]
    )


def _repositories(workspace: str) -> List[str]:
    from sqlmodel import Session, select

    from src.models.connected_repository import ConnectedRepository
    from src.models.repository import Repository
    from src.models.workspace import Workspace

    engine = get_engine()
    if engine is None or not workspace:
        return []
    with Session(engine) as session:
        return list(
            session.exec(
                select(Repository.full_name)
                .join(
                    ConnectedRepository,
                    ConnectedRepository.repository_id == Repository.id,
                )
                .join(Workspace, ConnectedRepository.workspace_id == Workspace.id)
                .where(Workspace.external_ref == workspace)
            ).all()
        )


def _owners(asked: Clearing, workspace: str) -> List[Owner]:
    """Whose entries this caller may drop, which never reaches past their own.

    The workspace comes from the token, so asking for another one clears
    nothing rather than somebody else's work.
    """
    # One rule for what this workspace holds, so what may be cleared and what
    # is cleared cannot disagree.
    connected = _repositories(workspace)

    if asked.scope_type == "repository":
        if not asked.scope_id:
            raise HTTPException(422, "A repository scope names a repository")
        if asked.scope_id not in connected:
            raise HTTPException(403, f"{asked.scope_id} is not connected here")
        return [Owner("repository", asked.scope_id)]

    if asked.scope_type and asked.scope_type != "workspace":
        raise HTTPException(422, "A scope is a repository or a workspace")
    if asked.scope_id and asked.scope_id != workspace:
        raise HTTPException(403, "A token clears only the workspace it names")

    # Scopes match exactly, so the repositories are named as well as the
    # workspace itself.
    return [Owner("workspace", workspace)] + [
        Owner("repository", name) for name in connected
    ]


@router.post("/clear")
def clear(asked: Clearing, scope: Scope = Depends(get_scope)):
    if asked.namespace not in CLEARABLE:
        raise HTTPException(404, f"Nothing is cached under {asked.namespace}")

    workspace = str(scope.get("workspace") or "")
    dropped = sum(
        cache().clear(asked.namespace, owner) for owner in _owners(asked, workspace)
    )
    return success_response(
        {
            "namespace": asked.namespace,
            "scope_type": asked.scope_type,
            "scope_id": asked.scope_id,
            "dropped": dropped,
        }
    )
