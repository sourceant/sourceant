"""JWT validation for service-to-service communication."""

import os

import jwt
from fastapi import Header, HTTPException

JWT_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is not set")
    return secret


async def get_current_user(authorization: str = Header(...)) -> dict:
    try:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        token = authorization[7:]
        payload = jwt.decode(
            token,
            _get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"], "verify_exp": True},
        )
        return {
            "user_id": payload["sub"],
            "github_token": payload.get("github_token"),
            "username": payload.get("username"),
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
