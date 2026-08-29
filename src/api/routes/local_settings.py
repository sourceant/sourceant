"""Settings for the machine this is running on.

The settings API proper is scoped to a user, a repository or an organisation,
and answers to a signed token that says which. Nobody signs in to their own
machine, so these routes are the same store at the user scope with one fixed
identity: whoever is sitting at it.

Gated like the rest of the local surface. See ``code.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.routes.code import require_local
from src.api.routes.settings import _described
from src.core.responses import success_response
from src.core.settings.definitions import USER
from src.core.settings.resolver import clear_value, resolve_all, set_value

router = APIRouter()

# One machine, one person, one place to keep what they chose. The scope has to
# be something, and anything derived from the machine would move the settings
# when a laptop is renamed.
WHOEVER_IS_HERE = "local"


@router.get("", dependencies=[Depends(require_local)])
def read_local_settings():
    """Everything configurable here, with what it is set to.

    A credential answers whether it is set rather than what it is. See
    ``settings.py``.
    """
    return success_response(
        [_described(resolved) for resolved in resolve_all(user=WHOEVER_IS_HERE)]
    )


class ValueInput(BaseModel):
    value: Any


@router.put("/{key}", dependencies=[Depends(require_local)])
def write_local_setting(key: str, body: ValueInput):
    """Give one setting a value on this machine."""
    try:
        return success_response(
            _described(set_value(USER, WHOEVER_IS_HERE, key, body.value))
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail=f"No setting called {key}"
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.delete("/{key}", dependencies=[Depends(require_local)])
def reset_local_setting(key: str):
    """Put one setting back to what it would be if nobody had touched it."""
    try:
        clear_value(USER, WHOEVER_IS_HERE, key)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail=f"No setting called {key}"
        ) from error
    return success_response({"key": key})
