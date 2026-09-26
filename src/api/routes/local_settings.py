"""Settings for the computer this runs on.

The settings API proper is scoped to a user, repository or organisation and
answers to a signed token. There is no sign-in here, so these routes are the
same store at the user scope under one fixed identity. Nothing about the
computer feeds into it, because a renamed machine would otherwise lose what was
configured on it.

Gated like the rest of the local surface. See ``code.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr

from src.api.routes.code import require_local
from src.api.routes.settings import _described, _described_at
from src.core.environment import LOCAL, environment
from src.core.model import SettingsLLMSource
from src.core.settings.configuration import Configuration
from src.core.responses import success_response
from src.core.settings.definitions import USER
from src.core.settings.resolver import clear_value, resolve_all, set_value

router = APIRouter()


def local_provider():
    """The model chosen here, ready to be asked, or None.

    Unlike a hosted deployment there is no fallback: the bill is the user's,
    so an unchosen model stays unchosen.
    """
    deployment = environment()
    if deployment is not None:
        return deployment.provider_for(deployment.workspace_for())
    return SettingsLLMSource(fallback_model="").provider_for(Configuration(user=LOCAL))


# Both of these wait on something outside this process, and neither awaits it.
# Declared without async, they are run on a thread and the loop stays free.
@router.get("/models", dependencies=[Depends(require_local)])
def local_models():
    """Every model that can be named here, by provider.

    Read from the router that would make the call rather than written down, so
    nobody has to remember how a provider spells its models.
    """
    from src.core.model.catalogue import offered

    return success_response(offered())


class ModelCheckInput(BaseModel):
    #: Left out, whatever is set here is used, so a pair already saved can be
    #: checked without sending the key again.
    model: str = ""
    api_key: SecretStr = SecretStr("")
    base_url: str = ""


@router.post("/models/check", dependencies=[Depends(require_local)])
def check_local_model(body: ModelCheckInput):
    """Whether this key can use this model, asked of the provider.

    A provider's catalogue says what exists. What an account may use is a
    subset, and the two only differ when somebody is already waiting.
    """
    from src.core.model.catalogue import refused

    configuration = Configuration(user=LOCAL)
    model = body.model or str(configuration.value("model.name") or "")
    key = body.api_key.get_secret_value() or str(
        configuration.value("model.api_key") or ""
    )
    base = body.base_url or str(configuration.value("model.base_url") or "")
    if not model:
        raise HTTPException(status_code=400, detail="Name a model")
    if not key:
        raise HTTPException(
            status_code=400, detail="No key is set here, and none was sent"
        )

    why = refused(model, key, base, attribution={"user": LOCAL})
    return success_response({"usable": why is None, "reason": why})


@router.get("", dependencies=[Depends(require_local)])
def read_local_settings():
    """Everything configurable here, with what it is set to.

    A credential answers whether it is set rather than what it is. See
    ``settings.py``.
    """
    settings = [
        _described_at(resolved, USER, LOCAL) for resolved in resolve_all(user=LOCAL)
    ]
    for setting in settings:
        if setting["secret"]:
            setting["is_set"] = setting["stored_is_set"]
    return success_response(settings)


class ValueInput(BaseModel):
    value: Any


@router.put("/{key}", dependencies=[Depends(require_local)])
def write_local_setting(key: str, body: ValueInput):
    """Give one setting a value."""
    try:
        return success_response(_described(set_value(USER, LOCAL, key, body.value)))
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
        clear_value(USER, LOCAL, key)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail=f"No setting called {key}"
        ) from error
    return success_response({"key": key})
