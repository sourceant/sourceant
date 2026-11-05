from contextlib import contextmanager
from contextvars import ContextVar

_actor: ContextVar[str | None] = ContextVar("acting_actor", default=None)


@contextmanager
def acting_as(actor: str):
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


def actor_in(scope):
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    return (
        (token.subject if token else None)
        or _actor.get()
        or scope.get("actor")
        or scope.get("agent")
        or scope.get("user")
        or "local"
    )


def actor_type_in(scope):
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    claims = (token.claims or {}) if token else {}
    scoped = claims.get("scope", {})
    agent = (
        scope.get("agent")
        or claims.get("agent")
        or claims.get("type") == "agent"
        or (isinstance(scoped, dict) and scoped.get("agent"))
    )
    return "agent" if agent else "person"
