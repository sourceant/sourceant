from __future__ import annotations

from typing import Callable, Protocol

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from src.auth import decode_access_token
from src.core.scope import Scope


class SourceAntTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        required_scopes: frozenset[str],
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._required_scopes = required_scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            payload = decode_access_token(
                token,
                issuer=self._issuer,
                audience=self._audience,
            )
        except jwt.InvalidTokenError:
            return None
        scopes = self._scopes(payload)
        if not self._required_scopes.issubset(scopes):
            return None
        subject = str(payload["sub"])
        return AccessToken(
            token=token,
            client_id=subject,
            scopes=sorted(scopes),
            expires_at=payload["exp"],
            subject=subject,
            claims=payload,
        )

    @staticmethod
    def _scopes(payload: dict) -> frozenset[str]:
        value = payload.get("scope", payload.get("scopes", ()))
        if isinstance(value, str):
            return frozenset(value.split())
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return frozenset(value)
        return frozenset()


class RepositoryEntitlement(Protocol):
    """Answers whether a principal may reach a repository, and who hosts it.

    Returning the provider is what lets the resolver name the repository the way
    the writers name it, from a request that gave only its full name.
    """

    def __call__(self, principal: str, repository: str) -> str | None: ...


class EntitledScopeResolver:
    """Check the caller against what they may reach, and leave the scope alone.

    The principal used to be added to the scope, and the scope is the key the
    graph is partitioned by, so a caller could only ever read back what that same
    caller had written through this server. Everything SourceAnt captures for
    itself is written without a principal, which put all of it in a partition no
    token could name.

    Isolation is now the entitlement rather than the partition, which is where it
    belongs: the tenant is read from a verified claim on the token and enforced
    on every call, and the data stays under the repository it is about.
    """

    def __init__(self, entitlement: RepositoryEntitlement) -> None:
        self._entitlement = entitlement

    def __call__(self, scope: Scope) -> Scope:
        token = get_access_token()
        if token is None or token.subject is None:
            raise ValueError("authenticated principal is required")

        # The workspace is a claim on the token, never something the caller sends
        # alongside a request, which is what keeps one from asking as another.
        workspace = (token.claims or {}).get("workspace")
        if not workspace:
            raise ValueError("this token names no workspace")

        repository = scope.get("repository")
        if not repository:
            raise ValueError("scope must name a repository")

        provider = self._entitlement(str(workspace), repository)
        if provider is None:
            raise ValueError(f"not entitled to {repository}")

        # A caller who named only the repository is asking about the same thing
        # as the writers, which name the provider too.
        return scope if scope.get("provider") else scope.extend({"provider": provider})


def connected_repository_entitlement(engine) -> Callable[[str, str], str | None]:
    """Entitlement as the rest of the API already means it: what the workspace
    connected.

    Connecting belongs to a workspace, so a token acts for a workspace. It is
    read from the token's own claim rather than from anything the caller sends
    with a request.
    """
    from sqlmodel import Session, select

    from src.models.connected_repository import ConnectedRepository
    from src.models.repository import Repository

    def entitled(workspace: str, repository: str) -> str | None:
        # Without a database there is nothing to check an entitlement against, so
        # the answer is no rather than an unchecked yes.
        if engine is None or not workspace:
            return None

        with Session(engine) as session:
            row = session.exec(
                select(Repository)
                .join(
                    ConnectedRepository,
                    ConnectedRepository.repository_id == Repository.id,
                )
                .where(
                    Repository.full_name == repository,
                    ConnectedRepository.workspace_id == str(workspace),
                )
            ).first()
        return row.provider if row else None

    return entitled
