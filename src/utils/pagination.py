"""One page of a list, in the shape every list endpoint answers with.

Returning a whole list works until the account grows, at which point the
response gets slower every week and the screen showing it has no way to ask for
less. A page carries the totals with it, so a reader can tell how much they are
looking at and ask for the rest.

The paging itself is fastapi-pagination's; these helpers only place its page
inside the response envelope the rest of the API uses.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi_pagination import Page, Params
from fastapi_pagination import paginate as _paginate_sequence
from fastapi_pagination.ext.sqlmodel import paginate as _paginate_query
from fastapi_pagination.utils import disable_installed_extensions_check
from sqlmodel import Session

# The database pages go through the sqlmodel extension below; without this the
# library warns about that on every in-memory page too.
disable_installed_extensions_check()

__all__ = ["Params", "page_of", "page_of_query", "as_data"]


def page_of(items: Sequence[Any], params: Params) -> dict:
    """A page of an already-gathered list."""
    return as_data(_paginate_sequence(items, params))


def page_of_query(session: Session, query: Any, params: Params) -> Page:
    """A page read straight from the database, so only the page is loaded."""
    return _paginate_query(session, query, params)


def as_data(page: Page, items: Sequence[Any] | None = None) -> dict:
    """
    A page as the envelope's data.

    Pass `items` to answer with something other than what came out of the query,
    which is what a route does when it enriches the rows it just read.
    """
    return {
        "items": list(page.items if items is None else items),
        "total": page.total,
        "page": page.page,
        "size": page.size,
        "pages": page.pages,
    }
