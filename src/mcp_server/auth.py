from __future__ import annotations

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


class PrincipalScopeResolver:
    def __call__(self, scope: Scope) -> Scope:
        token = get_access_token()
        if token is None or token.subject is None:
            raise ValueError("authenticated principal is required")
        return scope.extend({"principal": token.subject})
