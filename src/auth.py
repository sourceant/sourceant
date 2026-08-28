"""JWT validation for service-to-service communication."""

import os

import jwt
from fastapi import Header, HTTPException

from src.config.paths import local_jwt_secret
from src.config.settings import REQUIRE_GATEWAY

JWT_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if secret:
        return secret
    if REQUIRE_GATEWAY:
        raise RuntimeError(
            "JWT_SECRET is not set, and REQUIRE_GATEWAY says a gateway signs the "
            "tokens this verifies. Generating one here would reject every token "
            "the gateway sends."
        )
    return local_jwt_secret()


def require_jwt_secret() -> None:
    _get_jwt_secret()


def read_gateway_scope(authorization: str | None) -> dict | None:
    """The workspace a gateway signed this call for, or None if it did not sign one.

    Deliveries reach the agent through the gateway, which decides who is asking
    and whether they may be reviewed. The scope is that decision, carried across.
    """
    if not authorization:
        return None

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    try:
        payload = decode_access_token(authorization[7:])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    scope = payload.get("scope")
    return scope if isinstance(scope, dict) else {}


async def get_current_user(authorization: str = Header(...)) -> dict:
    try:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        token = authorization[7:]
        payload = decode_access_token(token)
        claimed_scope = payload.get("scope")
        return {
            "user_id": payload["sub"],
            "github_token": payload.get("github_token"),
            "username": payload.get("username"),
            "scope": claimed_scope if isinstance(claimed_scope, dict) else {},
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def decode_access_token(
    token: str,
    *,
    issuer: str | None = None,
    audience: str | None = None,
) -> dict:
    options = {"require": ["exp", "sub"], "verify_exp": True}
    if issuer is not None:
        options["require"].append("iss")
    if audience is not None:
        options["require"].append("aud")
    return jwt.decode(
        token,
        _get_jwt_secret(),
        algorithms=[JWT_ALGORITHM],
        issuer=issuer,
        audience=audience,
        options=options,
    )
