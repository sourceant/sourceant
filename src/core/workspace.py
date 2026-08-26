"""The workspace a call is acting in.

Everything a caller may reach is decided by which workspace they arrived in, and
that was being worked out in two places which disagreed about what to do when it
was missing. One place, one answer.
"""

from typing import Mapping, Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from src.models.connected_repository import ConnectedRepository
from src.models.workspace import Workspace


def workspace_in(claims: Mapping) -> Optional[str]:
    """The workspace a token names, wherever that token puts it.

    Two credentials reach this deployment and both are decoded with the same
    secret. The gateway signs a scope object and names it there; a token issued
    for an editor names it at the top level. A reader that knows only one of
    those refuses the other while it is looking straight at the answer.
    """
    scoped = claims.get("scope") or {}
    named = scoped.get("workspace_id") if isinstance(scoped, Mapping) else None
    return str(named or claims.get("workspace") or "") or None


def workspace_of(user: dict) -> str:
    """The workspace this call is acting in.

    The workspace is a claim on the token, never something sent alongside a
    request, so a call arriving without one cannot be answered rather than being
    answered about somebody's whole account.
    """
    workspace = workspace_in(user)
    if not workspace:
        raise HTTPException(status_code=400, detail="No workspace on this request")
    return workspace


def remember(session: Session, workspace: str) -> Workspace:
    """Record a workspace so that things can belong to it.

    Written on first sight rather than kept in step with the gateway. What is
    stored is that the workspace exists, which cannot go stale; anything else
    would be a copy of an answer the gateway already gives.
    """
    known = session.exec(
        select(Workspace).where(Workspace.external_id == workspace)
    ).first()
    if known is not None:
        return known

    known = Workspace(external_id=workspace)
    session.add(known)
    session.commit()
    session.refresh(known)
    return known


def connections_of(session: Session, workspace: str) -> list[ConnectedRepository]:
    """What a workspace has taken on, and when it took each one on.

    Joined through the identity the token names rather than compared against it
    directly: what belongs to a workspace points at the row, and only the row
    knows which name that is.
    """
    return list(
        session.exec(
            select(ConnectedRepository)
            .join(Workspace, ConnectedRepository.workspace_id == Workspace.id)
            .where(Workspace.external_id == workspace)
        ).all()
    )


def repositories_of(session: Session, workspace: str) -> list[int]:
    """Which repositories a workspace has taken on."""
    return [row.repository_id for row in connections_of(session, workspace)]


def connection_of(
    session: Session, workspace: str, repository_id: int
) -> Optional[ConnectedRepository]:
    """One workspace's hold on one repository, if it has one."""
    return session.exec(
        select(ConnectedRepository)
        .join(Workspace, ConnectedRepository.workspace_id == Workspace.id)
        .where(
            Workspace.external_id == workspace,
            ConnectedRepository.repository_id == repository_id,
        )
    ).first()
