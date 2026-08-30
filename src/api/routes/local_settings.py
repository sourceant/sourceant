"""Settings for the computer this runs on.

The settings API proper is scoped to a user, repository or organisation and
answers to a signed token. There is no sign-in here, so these routes are the
same store at the user scope with one fixed identity.

Gated like the rest of the local surface. See ``code.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.routes.code import require_local
from src.api.routes.settings import _described
from src.core.environment import environment
from src.core.model import SettingsModelSource
from src.core.responses import success_response
from src.core.settings.definitions import USER
from src.core.settings.resolver import clear_value, resolve_all, set_value

router = APIRouter()

# The scope has to be something, and anything derived from the computer would
# move the settings when it is renamed.
WHOEVER_IS_HERE = "local"


def model_for_this_machine():
    """The model chosen here, or None.

    Unlike a hosted deployment there is no fallback: the bill is the user's,
    so an unchosen model stays unchosen.
    """
    here = environment()
    if here is not None:
        return here.model_for(here.workspace_for())
    return SettingsModelSource(fallback_model="").model_for(user=WHOEVER_IS_HERE)


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
    """Give one setting a value."""
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
    """Put one setting back to its default."""
    try:
        clear_value(USER, WHOEVER_IS_HERE, key)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail=f"No setting called {key}"
        ) from error
    return success_response({"key": key})
