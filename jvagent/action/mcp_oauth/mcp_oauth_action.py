"""Action for managing no-code client OAuth flows for stdio MCP servers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from jvspatial.core.context import GraphContext
from jvspatial.db import get_database_manager

from jvagent.action.base import Action

from .mcp_oauth_node import MCPOAuthToken

logger = logging.getLogger(__name__)


async def _get_ctx() -> GraphContext:
    """Get a GraphContext for the database."""
    manager = get_database_manager()
    db = manager.get_database()
    return GraphContext(db)


from jvspatial.core.annotations import attribute

from jvagent.core.public_url import get_public_base_url


def _named_server_entries(servers: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in servers or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(raw)
    return out


def build_oauth_setup(
    base_url: str,
    servers: Optional[Sequence[Any]] = None,
    *,
    google_enabled_services: Optional[Iterable[str]] = None,
    microsoft_enabled_services: Optional[Iterable[str]] = None,
    account: str = "integral",
) -> List[Dict[str, Any]]:
    """Per-service OAuth endpoints for configured Google/Microsoft MCP servers."""
    from .hydrate import mcp_google_workspace_auth_url
    from .microsoft_hydrate import mcp_microsoft_365_auth_url
    from .microsoft_scopes import (
        MICROSOFT_365_SERVER,
    )
    from .microsoft_scopes import SERVICE_LABELS as MICROSOFT_SERVICE_LABELS
    from .microsoft_scopes import (
        microsoft_365_tool_config,
        microsoft_oauth_services,
    )
    from .scopes import (
        GOOGLE_SERVICE_LABELS,
        GOOGLE_WORKSPACE_SERVER,
        google_oauth_services,
        google_workspace_tool_config,
    )

    base = (base_url or "").strip().rstrip("/")
    if not base:
        return []

    entries = _named_server_entries(servers)
    names = [str(raw.get("name") or "").strip() for raw in entries]
    if not names:
        names = [GOOGLE_WORKSPACE_SERVER]

    setup: List[Dict[str, Any]] = []

    def _append(
        server: str,
        services: List[str],
        labels: Dict[str, str],
        auth_url_fn: Any,
    ) -> None:
        redirect = f"{base}/api/mcp/{server}/auth/callback"
        if services:
            for svc in services:
                setup.append(
                    {
                        "server": server,
                        "service": svc,
                        "label": labels.get(svc, svc),
                        "redirect_uri": redirect,
                        "auth_url": auth_url_fn(account, service=svc, base_url=base),
                    }
                )
            return
        setup.append(
            {
                "server": server,
                "redirect_uri": redirect,
                "auth_url": auth_url_fn(account, service=None, base_url=base),
            }
        )

    if GOOGLE_WORKSPACE_SERVER in names:
        if any(
            str(raw.get("name") or "").strip() == GOOGLE_WORKSPACE_SERVER
            for raw in entries
        ):
            tools, denied = google_workspace_tool_config(entries)
        else:
            tools, denied = [], []
        _append(
            GOOGLE_WORKSPACE_SERVER,
            google_oauth_services(
                tools, denied, enabled_services=google_enabled_services
            ),
            GOOGLE_SERVICE_LABELS,
            mcp_google_workspace_auth_url,
        )

    if MICROSOFT_365_SERVER in names:
        tools, denied = microsoft_365_tool_config(entries)
        _append(
            MICROSOFT_365_SERVER,
            microsoft_oauth_services(
                tools, denied, enabled_services=microsoft_enabled_services
            ),
            MICROSOFT_SERVICE_LABELS,
            mcp_microsoft_365_auth_url,
        )

    return setup


def mcp_oauth_state_action_id(account: str, service: str = "") -> str:
    """``mcp_oauth:{account}`` or ``mcp_oauth:{account}:{service}`` for OAuth state."""
    name = (account or "integral").strip() or "integral"
    svc = (service or "").strip()
    if svc:
        return f"mcp_oauth:{name}:{svc}"
    return f"mcp_oauth:{name}"


def parse_mcp_oauth_state_action_id(action_id: str) -> tuple[str, str]:
    """Return ``(account, service)`` from an MCP OAuth state action_id."""
    parts = (action_id or "").split(":")
    account = parts[1] if len(parts) > 1 and parts[1] else "integral"
    service = parts[2] if len(parts) > 2 else ""
    return account, service


_BLOB_KEYS = (
    "type",
    "refresh_token",
    "token",
    "scopes",
    "expiry",
    "token_uri",
    "client_id",
    "client_secret",
)
_PARENT_KEYS = ("email", "client_id", "client_secret", "account_alias", "type")


def _token_scopes(token: Dict[str, Any]) -> List[str]:
    raw = token.get("scopes")
    if raw is None:
        raw = token.get("scope")
    if isinstance(raw, str) and raw.strip():
        return raw.split()
    if isinstance(raw, list):
        return [str(s) for s in raw if str(s).strip()]
    return []


def _services_from_scopes(token: Dict[str, Any], server_name: str) -> List[str]:
    from .microsoft_scopes import MICROSOFT_365_SERVER, microsoft_services_from_scopes
    from .scopes import GOOGLE_WORKSPACE_SERVER, google_services_from_scopes

    scopes = _token_scopes(token)
    if server_name == GOOGLE_WORKSPACE_SERVER:
        return google_services_from_scopes(scopes)
    if server_name == MICROSOFT_365_SERVER:
        return microsoft_services_from_scopes(scopes)
    return []


def token_services(token: Dict[str, Any], server_name: str) -> List[str]:
    """Services this token owns. Prefer ``mcp_services``; else infer from scopes."""
    raw = token.get("mcp_services")
    if isinstance(raw, list):
        seen: set[str] = set()
        out: List[str] = []
        for item in raw:
            svc = str(item).strip()
            if svc and svc not in seen:
                seen.add(svc)
                out.append(svc)
        if out:
            return out
    st = _service_tokens_map(token)
    if st:
        return list(st)
    return _services_from_scopes(token, server_name)


def _service_tokens_map(token: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = token.get("service_tokens")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, blob in raw.items():
        svc = str(key).strip()
        if svc and isinstance(blob, dict):
            out[svc] = dict(blob)
    return out


def _credential_blob(token: Dict[str, Any]) -> Dict[str, Any]:
    blob: Dict[str, Any] = {}
    for key in _BLOB_KEYS:
        if key in token:
            blob[key] = token[key]
    return blob


def _flatten_service_blob(
    parent: Dict[str, Any], service: str, blob: Dict[str, Any]
) -> Dict[str, Any]:
    out = dict(blob)
    for key in _PARENT_KEYS:
        if not out.get(key) and parent.get(key):
            out[key] = parent[key]
    if not out.get("token_uri") and parent.get("token_uri"):
        out["token_uri"] = parent["token_uri"]
    out["mcp_services"] = [service]
    return out


def _dedupe_services(*groups: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for group in groups:
        for raw in group:
            svc = str(raw).strip()
            if svc and svc not in seen:
                seen.add(svc)
                out.append(svc)
    return out


def put_service_token(
    token: Dict[str, Any], service: str, blob: Dict[str, Any]
) -> Dict[str, Any]:
    """Write ``blob`` under ``service_tokens[service]`` without touching siblings."""
    svc = (service or "").strip()
    out = dict(token)
    st = _service_tokens_map(out)
    if svc:
        st[svc] = dict(blob)
        out["last_authorized_service"] = svc
    out["service_tokens"] = st
    out["mcp_services"] = _dedupe_services(
        [s for s in (out.get("mcp_services") or []) if s in st],
        list(st),
    )
    for key in ("refresh_token", "token", "scopes", "expiry"):
        if key in blob:
            out[key] = blob[key]
    return out


def _legacy_flat_covers_service(
    token: Dict[str, Any], service: str, server_name: str
) -> bool:
    """True when a pre-``service_tokens`` row can be used for ``service``."""
    claimed = token_services(token, server_name)
    if service not in claimed:
        return False
    evidenced = _services_from_scopes(token, server_name)
    if len(claimed) > 1:
        return evidenced == [service]
    if evidenced and service not in evidenced:
        return False
    return True


def service_token_payload(
    token: Optional[Dict[str, Any]],
    service: str,
    server_name: str,
) -> Optional[Dict[str, Any]]:
    """Flatten the blob for ``service``, or a compatible legacy flat payload."""
    if not token:
        return None
    svc = (service or "").strip()
    if not svc:
        return token
    st = _service_tokens_map(token)
    blob = st.get(svc)
    if isinstance(blob, dict):
        return _flatten_service_blob(token, svc, blob)
    if st:
        return None
    if not _legacy_flat_covers_service(token, svc, server_name):
        return None
    return token


def hydrate_credential_payload(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """Credential blob to write for MCP (one refresh token per email file)."""
    st = _service_tokens_map(token_data)
    if not st:
        return token_data
    last = str(token_data.get("last_authorized_service") or "").strip()
    if last and last in st:
        return _flatten_service_blob(token_data, last, st[last])
    if len(st) == 1:
        svc, blob = next(iter(st.items()))
        return _flatten_service_blob(token_data, svc, blob)
    return token_data


def _migrate_flat_to_service_tokens(
    token: Dict[str, Any], server_name: str
) -> Dict[str, Any]:
    if _service_tokens_map(token):
        return token
    out = dict(token)
    old_services = token_services(token, server_name)
    old_blob = _credential_blob(token)
    st: Dict[str, Dict[str, Any]] = {}
    if len(old_services) == 1:
        st[old_services[0]] = old_blob
    else:
        evidenced = _services_from_scopes(token, server_name)
        if len(evidenced) == 1 and evidenced[0] in old_services:
            st[evidenced[0]] = old_blob
    out["service_tokens"] = st
    if st:
        out["mcp_services"] = list(st.keys())
    return out


def oauth_bindings_from_tokens(
    server_name: str,
    rows: Sequence[Any],
) -> Dict[str, Dict[str, str]]:
    """Map stored MCP tokens to ``{service: {email}}`` without leaking secrets."""
    from .hydrate import account_email_from_token

    bindings: Dict[str, Dict[str, str]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        token = row.get("token")
        if not isinstance(token, dict):
            continue
        account = str(row.get("account_name") or "")
        email = account_email_from_token(token, account)
        if not email:
            continue
        for svc in token_services(token, server_name):
            bindings[svc] = {"email": email}
    return bindings


def apply_service_rebind(
    server_name: str,
    rows: Sequence[Any],
    *,
    account_name: str,
    token_data: Dict[str, Any],
    service: Optional[str] = None,
) -> List[tuple[str, Dict[str, Any]]]:
    """Store ``service`` on ``account_name`` without mixing refresh tokens.

    Does not delete other tokens or their refresh tokens. Returns
    ``(account_name, token)`` pairs to persist.
    """
    svc = (service or "").strip()
    incoming = dict(token_data)
    existing: Optional[Dict[str, Any]] = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("account_name") or "") != account_name:
            continue
        tok = row.get("token")
        if isinstance(tok, dict):
            existing = tok
        break

    incoming_st = _service_tokens_map(incoming)
    blob = _credential_blob(incoming)
    services_to_put = [svc] if svc else token_services(incoming, server_name)

    if existing:
        merged = _migrate_flat_to_service_tokens(dict(existing), server_name)
        for key in _PARENT_KEYS:
            if incoming.get(key):
                merged[key] = incoming[key]
        if incoming.get("token_uri"):
            merged["token_uri"] = incoming["token_uri"]
        if incoming_st:
            for name, svc_blob in incoming_st.items():
                merged = put_service_token(merged, name, svc_blob)
        elif services_to_put:
            for name in services_to_put:
                merged = put_service_token(merged, name, blob)
        else:
            for key in ("refresh_token", "token", "scopes", "expiry"):
                if key in incoming:
                    merged[key] = incoming[key]
    else:
        merged = dict(incoming)
        merged.pop("service_tokens", None)
        if incoming_st:
            merged["service_tokens"] = {}
            merged["mcp_services"] = []
            for name, svc_blob in incoming_st.items():
                merged = put_service_token(merged, name, svc_blob)
        elif services_to_put:
            merged["service_tokens"] = {}
            merged["mcp_services"] = []
            for name in services_to_put:
                merged = put_service_token(merged, name, blob)
        elif svc:
            merged = put_service_token(merged, svc, blob)

    claimed = {svc} if svc else set(merged.get("mcp_services") or [])
    out: List[tuple[str, Dict[str, Any]]] = [(account_name, merged)]
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        other = str(row.get("account_name") or "")
        if not other or other == account_name:
            continue
        tok = row.get("token")
        if not isinstance(tok, dict):
            continue
        current = token_services(tok, server_name)
        remaining = [s for s in current if s not in claimed]
        st = _service_tokens_map(tok)
        stripped_st = {k: v for k, v in st.items() if k not in claimed}
        if (
            remaining == current
            and stripped_st == st
            and isinstance(tok.get("mcp_services"), list)
        ):
            continue
        if (
            remaining == current
            and not claimed.intersection(current)
            and stripped_st == st
        ):
            continue
        updated = dict(tok)
        updated["mcp_services"] = remaining
        if st or stripped_st:
            updated["service_tokens"] = stripped_st
        out.append((other, updated))
    return out


def token_row_for_service(
    rows: Sequence[Any],
    server_name: str,
    service: str,
    fallback_account: str = "integral",
) -> tuple[Optional[str], Optional[Dict[str, Any]], Any]:
    """Pick the token that owns ``service``. Never steal another service's row."""
    svc = (service or "").strip()
    if svc:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            token = row.get("token")
            if not isinstance(token, dict):
                continue
            if svc not in token_services(token, server_name):
                continue
            payload = service_token_payload(token, svc, server_name)
            if payload is None:
                continue
            return row.get("account_name"), payload, row.get("node")
        return None, None, None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        token = row.get("token") if isinstance(row.get("token"), dict) else {}
        alias = token.get("account_alias") if isinstance(token, dict) else None
        if row.get("account_name") == fallback_account or alias == fallback_account:
            return row.get("account_name"), token, row.get("node")
    if not rows:
        return None, None, None
    row = rows[0]
    if not isinstance(row, dict):
        return None, None, None
    return row.get("account_name"), row.get("token"), row.get("node")


class MCPOAuthAction(Action):
    """Action that coordinates saving, loading, and refreshing OAuth tokens for MCP servers.

    This operates alongside jvagent/mcp to provide browser-based OAuth authorization
    mechanisms for stdio subprocess servers (like google-workspace-mcp).
    """

    oauth_setup: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Per-service OAuth endpoints. Each item: server, service, label, "
            "redirect_uri, auth_url."
        ),
    )

    # Endpoints prefix to tell FastAPI to mount these routes
    mcp_oauth_endpoint_path_prefixes: List[str] = [
        "/api/mcp/{server_name}/auth",
        "/api/mcp/{server_name}/auth/callback",
        "/api/mcp/{server_name}/auth/status",
    ]

    async def _enabled_action_services(
        self, action_services: Sequence[tuple]
    ) -> set[str]:
        enabled: set[str] = set()
        try:
            agent = await self.get_agent()
            if not agent:
                return enabled
            for type_name, service in action_services:
                sibling = await agent.get_action_by_type(type_name)
                if sibling and getattr(sibling, "enabled", True):
                    enabled.add(service)
        except Exception as exc:
            logger.warning(
                "Failed to list enabled sibling actions for MCP OAuth: %s", exc
            )
        return enabled

    async def _apply_env_defaults(self) -> None:
        from .microsoft_scopes import MICROSOFT_ACTION_SERVICES
        from .scopes import GOOGLE_ACTION_SERVICES

        servers: List[Any] = []
        try:
            ctx = await _get_ctx()
            from jvagent.action.mcp.mcp_action import MCPAction

            nodes = await ctx.find_nodes(MCPAction, {})
            mcp_action = nodes[0] if nodes else None
            if mcp_action:
                servers = list(getattr(mcp_action, "servers", None) or [])
        except Exception:
            servers = []

        google_enabled = await self._enabled_action_services(GOOGLE_ACTION_SERVICES)
        microsoft_enabled = await self._enabled_action_services(
            MICROSOFT_ACTION_SERVICES
        )
        self.oauth_setup = build_oauth_setup(
            get_public_base_url() or "",
            servers,
            google_enabled_services=google_enabled,
            microsoft_enabled_services=microsoft_enabled,
        )
        for stale in ("redirect_uri", "auth_url"):
            self.__dict__.pop(stale, None)
        await self.save()

    async def on_register(self) -> None:
        await self._apply_env_defaults()

    async def on_reload(self) -> None:
        await self._apply_env_defaults()

    async def on_startup(self) -> None:
        await self._apply_env_defaults()

    async def save_oauth_token(
        self,
        server_name: str,
        account_name: str,
        token_data: Dict[str, Any],
    ) -> None:
        """Create or update a token node in the graph database."""
        ctx = await _get_ctx()
        now = datetime.now(timezone.utc)

        # Search for existing token node
        filters = {
            "context.server_name": server_name,
            "context.account_name": account_name,
        }
        nodes = await ctx.find_nodes(MCPOAuthToken, filters)

        if nodes:
            node = nodes[0]
            node.token_json = json.dumps(token_data)
            node.updated = now
            logger.info(
                "Updating existing MCPOAuthToken for %s/%s", server_name, account_name
            )
        else:
            node = MCPOAuthToken(
                server_name=server_name,
                account_name=account_name,
                token_json=json.dumps(token_data),
                created=now,
                updated=now,
            )
            await node.set_context(ctx)
            logger.info(
                "Creating new MCPOAuthToken for %s/%s", server_name, account_name
            )

        await node.save()

        # Connect the token to the App node to keep graph structure valid
        from jvagent.core.app import App

        app = await App.get()
        if app:
            await app.connect(node)

        if server_name == "google_workspace":
            try:
                from .hydrate import (
                    account_email_from_token,
                    hydrate_google_workspace_account,
                )

                email = account_email_from_token(token_data, account_name)
                if email:
                    hydrate_google_workspace_account(email, token_data)
            except Exception as exc:
                logger.warning(
                    "Failed to hydrate google-workspace-mcp files for %s/%s: %s",
                    server_name,
                    account_name,
                    exc,
                )

    async def save_oauth_token_for_service(
        self,
        server_name: str,
        account_name: str,
        token_data: Dict[str, Any],
        service: Optional[str] = None,
    ) -> None:
        """Save ``token_data`` for ``account_name`` and unbind ``service`` elsewhere."""
        rows = await self.list_oauth_tokens(server_name)
        updates = apply_service_rebind(
            server_name,
            rows,
            account_name=account_name,
            token_data=token_data,
            service=service,
        )
        for acc, tok in updates:
            await self.save_oauth_token(server_name, acc, tok)

    async def get_oauth_token(
        self,
        server_name: str,
        account_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch the token data dictionary for a server/account pairing from the database."""
        ctx = await _get_ctx()

        filters = {
            "context.server_name": server_name,
            "context.account_name": account_name,
        }
        nodes = await ctx.find_nodes(MCPOAuthToken, filters)
        if not nodes:
            return None

        node = nodes[0]
        if not node.token_json:
            return None

        try:
            return json.loads(node.token_json)
        except Exception as exc:
            logger.error("Failed to parse stored token_json: %s", exc)
            return None

    async def list_oauth_tokens(self, server_name: str) -> List[Dict[str, Any]]:
        """Return ``{account_name, token}`` dicts for every token of ``server_name``."""
        ctx = await _get_ctx()
        nodes = await ctx.find_nodes(
            MCPOAuthToken, {"context.server_name": server_name}
        )
        out: List[Dict[str, Any]] = []
        for node in nodes or []:
            if not node.token_json:
                continue
            try:
                token = json.loads(node.token_json)
            except Exception:
                continue
            if not isinstance(token, dict):
                continue
            out.append(
                {"account_name": node.account_name, "token": token, "node": node}
            )
        return out

    async def oauth_bindings_for_server(
        self, server_name: str
    ) -> Dict[str, Dict[str, str]]:
        """Per-service connected emails for ``server_name`` (no token secrets)."""
        rows = await self.list_oauth_tokens(server_name)
        return oauth_bindings_from_tokens(server_name, rows)
