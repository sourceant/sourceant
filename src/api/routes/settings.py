"""Read and change what a user, repository, or organization has configured.

Every response says where a value came from, so a screen can show whether it is
set here, inherited, or simply the shipped default, and can offer to go back to
inheriting.
"""

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth import get_current_user
from src.core.responses import success_response
from src.core.settings import (
    ORGANIZATION,
    REPOSITORY,
    USER,
    Resolved,
    clear_value,
    for_scope,
    resolve_all,
    set_value,
)

router = APIRouter()

Scope = Literal["user", "repository", "organization"]


class SettingInput(BaseModel):
    value: Any


def _authorize_user_scope(scope: Scope, scope_id: str, user: dict) -> None:
    if scope == USER and str(user.get("user_id")) != scope_id:
        raise HTTPException(
            status_code=403, detail="User setting scope is not permitted"
        )


def _described(resolved: Resolved) -> dict:
    setting = resolved.setting
    secret = bool(setting and setting.secret)
    return {
        "key": resolved.key,
        # A credential is written like any other setting and never read back.
        # A screen needs to know whether one is set, which is a different
        # question from what it is, and answering the second would put it in a
        # log the first time somebody debugged the screen.
        "value": None if secret else resolved.value,
        "secret": secret,
        "listed": bool(setting and setting.listed),
        "is_set": bool(resolved.value) if secret else None,
        "source": resolved.source,
        "source_id": resolved.source_id,
        "label": setting.label if setting else resolved.key,
        "description": setting.description if setting else "",
        "type": setting.type if setting else "string",
        "default": setting.default if setting else None,
        "unit": setting.unit if setting else None,
        "minimum": setting.minimum if setting else None,
        "maximum": setting.maximum if setting else None,
        "choices": list(setting.choices) if setting else [],
        "group": setting.group if setting else "General",
    }


@router.get("/catalogue")
async def catalogue(
    scope: Scope = REPOSITORY,
    user: dict = Depends(get_current_user),
):
    """Everything that can be configured at this scope, without any values."""
    return success_response([asdict(setting) for setting in for_scope(scope)])


@router.get("/{scope}/{scope_id:path}")
async def read_settings(
    scope: Scope,
    scope_id: str,
    user: dict = Depends(get_current_user),
):
    """Every setting that applies here, resolved, with where each came from."""
    _authorize_user_scope(scope, scope_id, user)
    if scope == USER:
        resolved = resolve_all(user=scope_id)
    elif scope == REPOSITORY:
        resolved = resolve_all(repository=scope_id)
    else:
        resolved = resolve_all(organization=scope_id)
    return success_response([_described(item) for item in resolved])


@router.put("/{scope}/{scope_id:path}/{key}")
async def write_setting(
    scope: Scope,
    scope_id: str,
    key: str,
    payload: SettingInput,
    user: dict = Depends(get_current_user),
):
    """Give one setting a value here. Narrower scopes still win over this one."""
    _authorize_user_scope(scope, scope_id, user)
    try:
        resolved = set_value(scope, scope_id, key, payload.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error))
    return success_response(_described(resolved))


@router.delete("/{scope}/{scope_id:path}/{key}")
async def reset_setting(
    scope: Scope,
    scope_id: str,
    key: str,
    user: dict = Depends(get_current_user),
):
    """Stop setting this here, so it goes back to whatever it inherits."""
    _authorize_user_scope(scope, scope_id, user)
    try:
        clear_value(scope, scope_id, key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")

    if scope == USER:
        resolved = resolve_all(user=scope_id)
    elif scope == REPOSITORY:
        resolved = resolve_all(repository=scope_id)
    else:
        resolved = resolve_all(organization=scope_id)
    current = next((item for item in resolved if item.key == key), None)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")
    return success_response(_described(current))


__all__ = ["router", "ORGANIZATION", "REPOSITORY", "USER"]
