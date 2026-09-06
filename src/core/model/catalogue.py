"""Which models can be named, and which of them a key can actually use.

Two different questions, and answering only the first is what puts a model in
front of somebody that their account will refuse. The provider decides the
second, so it is asked rather than assumed.
"""

from __future__ import annotations

from typing import Optional

from src.utils.logger import logger


def offered() -> list[dict]:
    """Every model litellm can route to, by provider.

    Read from litellm rather than kept here, because a list written by hand goes
    out of date the week after it is written, and this one is already shipped
    with the thing that does the routing.

    litellm refreshes this from the network when it is imported and falls back
    to the copy it ships with otherwise, so a deployment with no outbound access
    offers an older list rather than none.
    """
    import litellm

    catalogue = []
    for provider, models in litellm.models_by_provider.items():
        named = sorted(models)
        if named:
            catalogue.append({"provider": provider, "models": named})
    catalogue.sort(key=lambda entry: entry["provider"])
    return catalogue


def refused(model: str, api_key: str, base_url: str = "") -> Optional[str]:
    """Why this key cannot use this model, or None when it can.

    A provider lists what exists; an account is entitled to a subset of it. The
    difference only shows on a real call, so a small one is made here rather
    than leaving it to be discovered halfway through reading a repository.
    """
    import litellm

    asked = {"model": model, "api_key": api_key}
    if base_url:
        asked["api_base"] = base_url
    try:
        litellm.completion(
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            **asked,
        )
        return None
    except Exception as error:
        logger.info(f"{model} was refused: {error}")
        return _plainly(error)


def _plainly(error: Exception) -> str:
    """The provider's own words, without the wrapping litellm adds to them."""
    said = str(error)
    for marker in ("Exception - ", "Error: "):
        _, found, rest = said.partition(marker)
        if found and rest:
            said = rest
    return said.strip().split("\n")[0][:300]
