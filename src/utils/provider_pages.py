"""Read a whole list from the provider, not just its first page.

The provider answers a list one page at a time and says in a header whether
there is another. Asking once and stopping returns a prefix that looks exactly
like a complete answer, so a reader cannot tell a short list from a truncated
one. These helpers follow the pages, and say plainly when they stopped early.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

import httpx

from src.utils.logger import logger

# What the provider allows per request.
PAGE_SIZE = 100

# Enough to hold any real repository or pull request, while keeping a runaway
# list from becoming an unbounded number of requests.
DEFAULT_MAX_PAGES = 20

_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def next_page_url(link_header: Optional[str]) -> Optional[str]:
    """The next page's URL from a Link header, or None when this is the last."""
    if not link_header:
        return None
    match = _NEXT_LINK.search(link_header)
    return match.group(1) if match else None


async def fetch_all(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    params: Optional[Mapping[str, Any]] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[Any], bool]:
    """
    Read every page the provider offers, up to a ceiling.

    Returns the items and whether more were left unread, so a caller can say so
    rather than presenting a prefix as the whole answer.
    """
    query: Optional[dict] = {"per_page": PAGE_SIZE, **(params or {})}
    items: list[Any] = []
    pages = 0

    while url and pages < max_pages:
        response = await client.get(url, headers=dict(headers), params=query)
        if response.status_code != 200:
            # A failure part way through returns what was read rather than
            # nothing, and reports that it is incomplete.
            logger.warning(f"{url} answered {response.status_code} while paging")
            return items, True

        try:
            page = response.json()
        except ValueError:
            # A proxy error page, or a body cut off part way through. Keeping
            # what was read and saying the list is short beats handing back a
            # prefix that looks whole.
            logger.warning(f"{url} answered something other than JSON while paging")
            return items, True

        if not isinstance(page, list):
            logger.warning(
                f"{url} answered a {type(page).__name__} where a list was expected"
            )
            return items, True

        items.extend(page)
        pages += 1

        url = next_page_url(response.headers.get("link"))
        # The next URL already carries its own query, and passing an empty set
        # of parameters would replace it rather than leave it alone.
        query = None

    truncated = bool(url)
    if truncated:
        logger.info(f"Stopped after {pages} pages; more were available")
    return items, truncated
