"""Which models can be named, and which of them a key can actually use.

Two different questions, and answering only the first is what puts a model in
front of somebody that their account will refuse. The provider decides the
second, so it is asked rather than assumed.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as TookTooLong
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from src.config.settings import whole_number
from src.utils.logger import logger

# How long a provider gets to answer a check. Unbounded, a provider that has
# stopped answering holds the thread until something else gives up first.
PROBE_SECONDS = whole_number("LLM_PROBE_TIMEOUT", 15)

# How long a name gets to resolve, for the same reason.
RESOLVE_SECONDS = whole_number("LLM_RESOLVE_TIMEOUT", 5)

# A deployment running its own models reaches them on an address only it can
# see, so an operator can say so and have private addresses accepted. Off by
# default, because anywhere a person other than the operator names an endpoint,
# turning it on hands them this server as a way to reach that network.
PRIVATE_ENDPOINTS = os.getenv("LLM_ALLOW_PRIVATE_ENDPOINTS", "").lower() in (
    "1",
    "true",
    "yes",
)


@lru_cache(maxsize=1)
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


def reachable(url: str) -> str:
    """The endpoint, if this server may be asked to call it.

    Whatever is named here is somewhere this server then sends a request to,
    carrying whatever key came with it. Left open, it answers questions about
    the network it sits in on behalf of anybody who can reach it.

    A name is resolved to decide, and a name can resolve differently a moment
    later, so this narrows the opening rather than closing it.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base_url must be an http or https address")
    if PRIVATE_ENDPOINTS:
        return url
    for address in _addresses(parsed.hostname):
        if not address.is_global:
            raise ValueError("base_url must be an address on the public internet")
    return url


def _addresses(host: str) -> list:
    """Every address a name answers with, because one of them is enough.

    Resolving has no timeout of its own, so it is waited on rather than called
    directly. The lookup itself cannot be cancelled; this bounds how long
    anybody waits on it, not how long it runs.
    """
    asking = ThreadPoolExecutor(max_workers=1)
    try:
        found = asking.submit(socket.getaddrinfo, host, None).result(
            timeout=RESOLVE_SECONDS
        )
    except TookTooLong as slow:
        raise ValueError("base_url did not resolve in time") from slow
    except socket.gaierror as unknown:
        raise ValueError("base_url does not resolve") from unknown
    finally:
        asking.shutdown(wait=False)
    return [ipaddress.ip_address(entry[4][0]) for entry in found]


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
            timeout=PROBE_SECONDS,
            # One question, asked once. Retrying a provider that is not
            # answering turns a bounded wait into several of them, and the
            # client underneath retries on its own unless it is told not to.
            num_retries=0,
            max_retries=0,
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
