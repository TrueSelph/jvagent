"""Trusted-origin checks for credentialed MCP httpx redirect hops.

httpx follows redirects with ``follow_redirects=True``. It strips standard
``Authorization`` on cross-origin hops, but custom ``X-Integral-*`` headers are
forwarded. Request hooks that re-attach credentials must therefore only run for
the configured MCP origin, and untrusted 3xx targets must be rejected before the
next hop is sent.
"""

from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import urlsplit

import httpx

# (scheme_lower, hostname_lower, port)
Origin = Tuple[str, str, int]

_SERVICE_KEY_HEADERS = (
    "x-integral-service-key",
    "x-integral-user-id",
    "x-integral-timestamp",
    "x-integral-signature",
    "x-integral-scope",
)


class UntrustedRedirectError(RuntimeError):
    """Raised when a redirect would leave the trusted MCP origin."""


def origin_of(url: str) -> Optional[Origin]:
    """Return ``(scheme, host, port)`` for *url*, or ``None`` if incomplete."""
    raw = (url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    port = parts.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else 0
    return scheme, host, int(port)


def is_trusted_url(url: str, trusted: Optional[Origin]) -> bool:
    """True when *url* matches *trusted* and is not an HTTPS→HTTP downgrade."""
    if trusted is None:
        return False
    current = origin_of(url)
    if current is None:
        return False
    scheme, host, port = current
    t_scheme, t_host, t_port = trusted
    if scheme == "http" and t_scheme == "https":
        return False
    return scheme == t_scheme and host == t_host and port == t_port


def strip_authorization_headers(request: httpx.Request) -> None:
    for name in ("authorization", "x-integral-scope"):
        if name in request.headers:
            del request.headers[name]


def strip_service_key_headers(request: httpx.Request) -> None:
    for name in _SERVICE_KEY_HEADERS:
        if name in request.headers:
            del request.headers[name]


def make_redirect_validator(trusted: Optional[Origin]):
    """Return an httpx response hook that rejects untrusted 3xx targets."""

    async def _validate_redirect(response: httpx.Response) -> None:
        if not (300 <= response.status_code < 400):
            return
        loc = response.headers.get("location")
        if not loc:
            return
        target = str(httpx.URL(response.url).join(loc))
        baseline = trusted or origin_of(str(response.url))
        if not is_trusted_url(target, baseline):
            raise UntrustedRedirectError(
                f"Refusing credentialed MCP redirect to untrusted origin: {target}"
            )

    return _validate_redirect
