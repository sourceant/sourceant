"""Which MCP server this is: who may reach it, and as whom."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from src.core.environment import HOSTED, LOCAL
from src.core.scope import Scope


@dataclass(frozen=True)
class Surface:
    """How one MCP server differs from the other.

    Hosted is reachable by anyone with a token and answers within the workspace
    that token names. Personal is reachable over loopback or stdio only, serves
    one workspace, and can read a checkout off the disk.

    FastMCP raises unless auth and token_verifier are both present or both
    absent, so a half-configured surface is refused here instead.
    """

    environment: str
    auth: Optional[AuthSettings] = None
    token_verifier: Optional[TokenVerifier] = None
    scope_resolver: Optional[Callable[[Scope], Scope]] = None
    # Whether this server can read files off a disk the caller is sitting at.
    reaches_checkout: bool = False
    transport_security: Optional[TransportSecuritySettings] = None

    def __post_init__(self) -> None:
        if bool(self.auth) != bool(self.token_verifier):
            raise ValueError(
                "an MCP surface needs both auth settings and a token verifier, "
                "or neither"
            )
        if self.environment == HOSTED and not self.auth:
            raise ValueError("a hosted MCP surface must be authenticated")

    @property
    def authenticated(self) -> bool:
        return self.auth is not None


# Loopback only. Unauthenticated and reachable from a browser otherwise: the
# SDK leaves DNS-rebinding protection off by default, so a page the user visits
# could reach 127.0.0.1/mcp and write knowledge or read a checkout.
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


def personal_surface(*, reaches_checkout: bool = True) -> Surface:
    # Any loopback port: core is reached directly on one and through the
    # agent's proxy on another, and both are the same computer.
    hosts = [f"{host}:*" for host in LOOPBACK] + list(LOOPBACK)
    return Surface(
        environment=LOCAL,
        reaches_checkout=reaches_checkout,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[f"http://{host}" for host in hosts],
        ),
    )


def hosted_surface(
    auth: AuthSettings,
    token_verifier: TokenVerifier,
    scope_resolver: Callable[[Scope], Scope],
) -> Surface:
    return Surface(
        environment=HOSTED,
        auth=auth,
        token_verifier=token_verifier,
        scope_resolver=scope_resolver,
        reaches_checkout=False,
    )
