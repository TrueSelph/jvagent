"""Generic OAuth 2.1 provider for Integral Core's MCP endpoint.

Unlike the Google/Microsoft providers (:mod:`hydrate`, :mod:`microsoft_hydrate`),
which use a *static* pre-registered ``client_id`` and hydrate token files onto a
stdio subprocess, Integral Core is an **OAuth 2.1 authorization server** in the
sense of the MCP authorization spec:

* **Dynamic Client Registration** (RFC 7591) with open registration and public
  clients (``token_endpoint_auth_method=none``) — no client secret.
* **PKCE** (RFC 7636) with ``S256`` — mandatory for public clients.
* **Discovery** via ``/.well-known/oauth-protected-resource`` (RFC 9728) and
  ``/.well-known/oauth-authorization-server`` (RFC 8414), both served at the
  Core root.
* The resulting access token is used directly as ``Authorization: Bearer`` on
  the ``streamable_http`` MCP client (see :mod:`jvagent.action.mcp.integral_oauth_auth`);
  there is no disk hydration step.

Endpoints (Core defaults, see ``app/main.py`` / jvspatial ``oauth_prefix``):
authorize ``/api/oauth/authorize``, token ``/api/oauth/token``, register
``/api/oauth/register``.  Discovery is preferred; the literal path layout is only
a fallback for when the ``/.well-known`` documents are not reachable (e.g. a
reverse proxy that forwards only ``/api``).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, cast
from urllib.parse import urlencode, urlparse

import httpx
from jvspatial.core import Node
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)

INTEGRAL_SERVER = "integral_core"
INTEGRAL_SERVICE = "integral"
INTEGRAL_LABEL = "Integral"
DEFAULT_ACCOUNT = "integral"

# Fallback OAuth path prefix when discovery documents are unreachable.  Matches
# jvspatial's advertised ``{issuer}/api/oauth/*`` layout (AuthConfig default
# ``oauth_prefix="/oauth"`` mounted under the ``/api`` API prefix).
_FALLBACK_OAUTH_PREFIX = "/api/oauth"

DEFAULT_TIMEOUT = 15.0

# Treat a token as expired this many seconds before its real expiry so a
# request never races the clock.
_EXPIRY_SKEW_SECONDS = 60


class IntegralOAuthServiceError(RuntimeError):
    """Raised when discovery / registration / token exchange fails."""


class IntegralOAuthClient(Node):
    """Persisted Dynamic Client Registration for one Integral MCP server.

    A DCR registration is reusable across connect flows; persisting it avoids
    re-registering on every auth-page load and sidesteps the server's DCR
    rate limit.
    """

    server_name: str = attribute(
        indexed=True,
        default=INTEGRAL_SERVER,
        description="MCP server name this registration belongs to.",
    )
    client_id: str = attribute(
        default="",
        description="DCR-issued public client identifier.",
    )
    redirect_uri: str = attribute(
        default="",
        description="Redirect URI the client was registered with.",
    )
    scope: str = attribute(
        default="",
        description="Space-delimited scope registered with the client.",
    )
    metadata_json: str = attribute(
        default="",
        description="Serialized authorization-server metadata discovered at register time.",
    )


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def mcp_integral_core_auth_url(
    account: str = DEFAULT_ACCOUNT,
    service: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Public browser URL for the Integral MCP OAuth start page."""
    from jvagent.core.public_url import get_public_base_url

    base = (base_url or get_public_base_url() or "").rstrip("/")
    params = []
    if account:
        params.append(f"account={account}")
    if service:
        params.append(f"service={service}")
    q = f"?{'&'.join(params)}" if params else ""
    return f"{base}/api/mcp/{INTEGRAL_SERVER}/auth{q}"


def _origin(url: str) -> str:
    """Return ``scheme://host[:port]`` for *url* (no trailing slash)."""
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def resolve_mcp_url(mcp_url: str = "") -> str:
    """Resolve the Integral MCP URL from config or the environment.

    Priority: explicit *mcp_url* → ``INTEGRAL_CORE_MCP_URL`` →
    ``INTEGRAL_MCP_URL`` → ``http://127.0.0.1:9006/api/mcp/`` (Core's stable web
    port via the bundled reverse proxy).
    """
    candidate = (mcp_url or "").strip()
    if not candidate:
        candidate = (
            os.environ.get("INTEGRAL_CORE_MCP_URL")
            or os.environ.get("INTEGRAL_MCP_URL")
            or ""
        ).strip()
    return candidate or "http://127.0.0.1:9006/api/mcp/"


def resolve_issuer_override(issuer: str = "") -> str:
    """Resolve an explicit authorization-server issuer, if configured."""
    candidate = (issuer or "").strip()
    if not candidate:
        candidate = (os.environ.get("INTEGRAL_OAUTH_ISSUER") or "").strip()
    return candidate.rstrip("/")


def resolve_scope(scope: str = "") -> str:
    """Resolve the OAuth scope string (may legitimately be empty)."""
    candidate = (scope or "").strip()
    if not candidate:
        candidate = (os.environ.get("INTEGRAL_OAUTH_SCOPE") or "").strip()
    return candidate


# Scopes are authored on the MCP server entry (``auth.scopes`` / ``auth.scope``)
# and forwarded to Core *verbatim* — no aliasing or filtering. Core intersects
# the requested scope with its own ``OAUTH_SUPPORTED_SCOPES`` ceiling, so adding
# or renaming a scope never requires a code change here.


def join_scopes(value: Any) -> str:
    """Join a scope config value into a space-separated string, verbatim.

    Accepts a list/tuple/set (or a space/comma-separated string). Each token is
    stripped and passed through unchanged — no aliasing, case-folding, or
    filtering — so any scope Core advertises (or later adds) can be authored
    directly on the server entry. Order is preserved; empties are dropped.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        tokens = [t for t in re.split(r"[,\s]+", value) if t]
    elif isinstance(value, (list, tuple, set)):
        tokens = [str(t) for t in value if str(t).strip()]
    else:
        tokens = [str(value)]
    return " ".join(str(t).strip() for t in tokens if str(t).strip())


def resolve_scopes_from_auth(auth_cfg: Optional[Dict[str, Any]]) -> str:
    """Resolve the requested scope from a server ``auth`` block, else the env.

    Precedence: ``auth.scopes`` (list) → ``auth.scope`` (string) →
    ``INTEGRAL_OAUTH_SCOPE`` env (via :func:`resolve_scope`). Values are passed
    through verbatim.
    """
    if isinstance(auth_cfg, dict):
        if auth_cfg.get("scopes"):
            return join_scopes(auth_cfg.get("scopes"))
        if auth_cfg.get("scope"):
            return join_scopes(auth_cfg.get("scope"))
    return resolve_scope()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def fallback_metadata(origin: str) -> Dict[str, Any]:
    """Construct AS metadata from the origin when discovery docs are unreachable."""
    base = origin.rstrip("/")
    ep_base = f"{base}{_FALLBACK_OAUTH_PREFIX}"
    return {
        "issuer": base,
        "authorization_endpoint": f"{ep_base}/authorize",
        "token_endpoint": f"{ep_base}/token",
        "registration_endpoint": f"{ep_base}/register",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
    }


async def _get_json(client: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
    """GET *url* and return a JSON object, or ``None`` on any failure."""
    try:
        resp = await client.get(url, headers={"Accept": "application/json"})
    except Exception as exc:  # network / DNS / TLS
        logger.debug("Integral OAuth discovery GET failed (%s): %s", url, exc)
        return None
    if resp.status_code != 200:
        logger.debug("Integral OAuth discovery GET %s -> %s", url, resp.status_code)
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


async def discover_authorization_server(
    *,
    mcp_url: str = "",
    issuer: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Discover Core's authorization-server metadata.

    Order: explicit *issuer* override → ``/.well-known/oauth-protected-resource``
    → ``/.well-known/oauth-authorization-server`` → a constructed fallback based
    on the MCP origin.  Always returns a usable dict (raising only when no origin
    can be determined).
    """
    override = resolve_issuer_override(issuer)
    if override:
        return await _metadata_for_issuer(override, timeout=timeout)

    origin = _origin(resolve_mcp_url(mcp_url))
    if not origin:
        raise IntegralOAuthServiceError(
            "Cannot discover Integral OAuth endpoints: no MCP URL configured."
        )

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        prm = await _get_json(client, f"{origin}/.well-known/oauth-protected-resource")
        as_issuer = ""
        if prm:
            servers = prm.get("authorization_servers") or []
            if isinstance(servers, list) and servers:
                as_issuer = str(servers[0]).strip().rstrip("/")

        if as_issuer and as_issuer != origin:
            meta = await _metadata_for_issuer(as_issuer, timeout=timeout)
            if meta.get("token_endpoint"):
                return meta

        direct = await _get_json(
            client, f"{origin}/.well-known/oauth-authorization-server"
        )
        if direct and direct.get("token_endpoint"):
            return direct

    logger.warning(
        "Integral OAuth discovery docs unreachable at %s; "
        "falling back to %s/* layout",
        origin,
        _FALLBACK_OAUTH_PREFIX,
    )
    return fallback_metadata(origin)


async def _metadata_for_issuer(
    issuer: str, *, timeout: float = DEFAULT_TIMEOUT
) -> Dict[str, Any]:
    """Fetch AS metadata for a known *issuer*, else synthesize it."""
    issuer = issuer.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        meta = await _get_json(
            client, f"{issuer}/.well-known/oauth-authorization-server"
        )
    if meta and meta.get("token_endpoint"):
        return meta
    return fallback_metadata(issuer)


# ---------------------------------------------------------------------------
# Dynamic Client Registration
# ---------------------------------------------------------------------------


async def register_client(
    meta: Dict[str, Any],
    *,
    redirect_uri: str,
    scope: str = "",
    client_name: str = "jvagent",
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Register a public client via DCR; return the ``client_id``."""
    endpoint = str(meta.get("registration_endpoint") or "").strip()
    if not endpoint:
        raise IntegralOAuthServiceError(
            "Authorization-server metadata has no registration_endpoint."
        )
    body: Dict[str, Any] = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scope:
        body["scope"] = scope
    if "S256" in (meta.get("code_challenge_methods_supported") or []):
        pass  # PKCE S256 is advertised; nothing to declare at registration.

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            endpoint, json=body, headers={"Accept": "application/json"}
        )
    if resp.status_code not in (200, 201):
        raise IntegralOAuthServiceError(
            f"Dynamic client registration failed ({resp.status_code}): {resp.text}"
        )
    data = resp.json()
    client_id = str((data or {}).get("client_id") or "").strip()
    if not client_id:
        raise IntegralOAuthServiceError(
            "Dynamic client registration response carried no client_id."
        )
    return client_id


async def load_registered_client(
    server_name: str = INTEGRAL_SERVER,
) -> Optional[IntegralOAuthClient]:
    """Return the persisted DCR client for *server_name*, if any."""
    try:
        rows = await IntegralOAuthClient.find({"context.server_name": server_name})
    except Exception as exc:
        logger.debug("Integral DCR client lookup failed: %s", exc)
        return None
    return cast(IntegralOAuthClient, rows[0]) if rows else None


async def save_registered_client(
    *,
    server_name: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    meta: Dict[str, Any],
) -> None:
    """Persist (or update) the DCR client for *server_name*."""
    import json

    existing = await load_registered_client(server_name)
    payload = json.dumps(meta or {})
    if existing:
        existing.client_id = client_id
        existing.redirect_uri = redirect_uri
        existing.scope = scope
        existing.metadata_json = payload
        await existing.save()
        return
    await IntegralOAuthClient.create(
        server_name=server_name,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        metadata_json=payload,
    )


async def ensure_registered_client(
    *,
    server_name: str = INTEGRAL_SERVER,
    redirect_uri: str,
    scope: str = "",
    mcp_url: str = "",
    issuer: str = "",
    force: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Return ``(client_id, as_metadata)``, registering via DCR when needed."""
    existing = None if force else await load_registered_client(server_name)
    if (
        existing
        and existing.client_id
        and existing.redirect_uri == redirect_uri
        and (existing.scope or "") == scope
    ):
        meta = await discover_authorization_server(mcp_url=mcp_url, issuer=issuer)
        return existing.client_id, meta

    meta = await discover_authorization_server(mcp_url=mcp_url, issuer=issuer)
    client_id = await register_client(meta, redirect_uri=redirect_uri, scope=scope)
    await save_registered_client(
        server_name=server_name,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        meta=meta,
    )
    logger.info(
        "Registered Integral OAuth client for %s (client_id=%s…)",
        server_name,
        client_id[:8],
    )
    return client_id, meta


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def pkce_verifier(nbytes: int = 64) -> str:
    """Return an RFC 7636 ``code_verifier`` (43-128 unreserved characters)."""
    return secrets.token_urlsafe(nbytes)


def pkce_challenge(verifier: str) -> str:
    """Return the ``S256`` ``code_challenge`` for *verifier*."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Authorization URL, token exchange, refresh
# ---------------------------------------------------------------------------


def build_authorize_url(
    meta: Dict[str, Any],
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str = "",
) -> str:
    """Build the authorization-code URL with a PKCE S256 challenge."""
    endpoint = str(meta.get("authorization_endpoint") or "").strip()
    if not endpoint:
        raise IntegralOAuthServiceError(
            "Authorization-server metadata has no authorization_endpoint."
        )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    return f"{endpoint}?{urlencode(params)}"


async def exchange_authorization_code(
    meta: Dict[str, Any],
    *,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Exchange an authorization code for tokens (public client + PKCE)."""
    endpoint = str(meta.get("token_endpoint") or "").strip()
    if not endpoint:
        raise IntegralOAuthServiceError(
            "Authorization-server metadata has no token_endpoint."
        )
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            endpoint, data=data, headers={"Accept": "application/json"}
        )
    if resp.status_code != 200:
        raise IntegralOAuthServiceError(
            f"Integral token exchange failed ({resp.status_code}): {resp.text}"
        )
    tokens = resp.json()
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise IntegralOAuthServiceError(
            "Integral token exchange returned no access_token."
        )
    return tokens


async def refresh_access_token(
    meta: Dict[str, Any],
    *,
    client_id: str,
    refresh_token: str,
    scope: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Refresh an access token (public client; no PKCE on refresh)."""
    endpoint = str(meta.get("token_endpoint") or "").strip()
    if not endpoint:
        raise IntegralOAuthServiceError(
            "Authorization-server metadata has no token_endpoint."
        )
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if scope:
        data["scope"] = scope
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            endpoint, data=data, headers={"Accept": "application/json"}
        )
    if resp.status_code != 200:
        raise IntegralOAuthServiceError(
            f"Integral token refresh failed ({resp.status_code}): {resp.text}"
        )
    tokens = resp.json()
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise IntegralOAuthServiceError(
            "Integral token refresh returned no access_token."
        )
    return tokens


# ---------------------------------------------------------------------------
# Stored token payload helpers
# ---------------------------------------------------------------------------


def normalize_token_payload(
    tokens: Dict[str, Any],
    *,
    client_id: str,
    token_uri: str,
    account_name: str,
    scope: str = "",
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the JSON payload persisted on :class:`MCPOAuthToken`.

    ``refresh`` tokens are rotated by some servers; keep the previous value when
    the response omits a new one.
    """
    expires_in = int(tokens.get("expires_in") or 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=max(0, expires_in))
    refresh = tokens.get("refresh_token") or (
        (previous or {}).get("refresh_token") if previous else ""
    )
    scopes = tokens.get("scope")
    if isinstance(scopes, str):
        scope_list = scopes.split()
    else:
        scope_list = list(scope.split()) if scope else []
    return {
        "type": "oauth2",
        "client_id": client_id,
        "token": tokens.get("access_token") or "",
        "access_token": tokens.get("access_token") or "",
        "refresh_token": refresh or "",
        "token_uri": token_uri,
        "scopes": scope_list,
        "account_alias": account_name,
        "expiry": expiry.isoformat(),
    }


def payload_is_expired(payload: Optional[Dict[str, Any]]) -> bool:
    """Return True when the stored access token is missing or (near) expired."""
    if not payload:
        return True
    if not payload.get("access_token") and not payload.get("token"):
        return True
    raw = payload.get("expiry") or ""
    if not raw:
        return False
    try:
        expiry = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc) + timedelta(
        seconds=_EXPIRY_SKEW_SECONDS
    )
