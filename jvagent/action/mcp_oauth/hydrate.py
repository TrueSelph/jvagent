"""Write MCPOAuthToken payloads onto google-workspace-mcp XDG paths.

@aaronsb/google-workspace-mcp reads:

- ``$XDG_CONFIG_HOME/google-workspace-mcp/accounts.json``
- ``$XDG_DATA_HOME/google-workspace-mcp/credentials/<email_slug>.json``

falling back to ``~/.config`` and ``~/.local/share``. Hydration must run before
the stdio subprocess is spawned so the MCP server sees the same tokens the
graph already stored.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

APP_NAME = "google-workspace-mcp"
TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_WORKSPACE_SERVER = "google_workspace"


def xdg_config_home() -> Path:
    raw = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".config"


def xdg_data_home() -> Path:
    raw = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def email_to_slug(email: str) -> str:
    """Match google-workspace-mcp ``emailToSlug`` (strip path seps, @ → _at_, . → _dot_)."""
    safe = (email or "").replace("/", "").replace("\\", "")
    return safe.replace("@", "_at_").replace(".", "_dot_")


def accounts_file_path() -> Path:
    return xdg_config_home() / APP_NAME / "accounts.json"


def credential_path(email: str) -> Path:
    return xdg_data_home() / APP_NAME / "credentials" / f"{email_to_slug(email)}.json"


def mcp_google_workspace_auth_url(
    account: str = "integral",
    service: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Public browser URL for the MCP Google OAuth start page."""
    from jvagent.core.public_url import get_public_base_url

    base = (base_url or get_public_base_url() or "").rstrip("/")
    params: List[str] = []
    if account:
        params.append(f"account={account}")
    if service:
        params.append(f"service={service}")
    q = f"?{'&'.join(params)}" if params else ""
    return f"{base}/api/mcp/{GOOGLE_WORKSPACE_SERVER}/auth{q}"


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_private(path: Path, text: str) -> None:
    _mkdir_private(path.parent)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def upsert_account_registry(
    email: str, *, category: str = "work", description: str = ""
) -> None:
    """Merge ``email`` into accounts.json without dropping other accounts."""
    path = accounts_file_path()
    data: Dict[str, Any] = {"accounts": []}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("accounts"), list):
                data = loaded
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read MCP accounts.json: %s", exc)

    accounts: List[Dict[str, Any]] = list(data.get("accounts") or [])
    found = False
    for entry in accounts:
        if isinstance(entry, dict) and entry.get("email") == email:
            if category:
                entry["category"] = category
            if description:
                entry["description"] = description
            found = True
            break
    if not found:
        rec: Dict[str, Any] = {"email": email, "category": category or "work"}
        if description:
            rec["description"] = description
        accounts.append(rec)
    data["accounts"] = accounts
    _write_private(path, json.dumps(data, indent=2))


def write_credential_file(email: str, token_data: Dict[str, Any]) -> Path:
    """Write an ``authorized_user`` credential file for ``email``."""
    payload = {
        "type": "authorized_user",
        "client_id": token_data.get("client_id") or "",
        "client_secret": token_data.get("client_secret") or "",
        "refresh_token": token_data.get("refresh_token") or "",
    }
    scopes = token_data.get("scopes")
    if scopes:
        payload["scopes"] = list(scopes)
    dest = credential_path(email)
    _write_private(dest, json.dumps(payload, indent=2))
    return dest


def hydrate_google_workspace_account(
    email: str,
    token_data: Dict[str, Any],
    *,
    category: str = "work",
    description: str = "",
) -> None:
    """Write accounts.json + the per-account credential file for one account."""
    from .mcp_oauth_action import hydrate_credential_payload

    email = (email or "").strip()
    if not email:
        raise ValueError(
            "Cannot hydrate google-workspace-mcp credentials without an email"
        )
    payload = hydrate_credential_payload(token_data)
    if not payload.get("refresh_token"):
        raise ValueError(f"No refresh_token to hydrate for {email}")
    upsert_account_registry(email, category=category, description=description)
    write_credential_file(email, payload)
    logger.info("Hydrated google-workspace-mcp credentials for %s", email)


def account_email_from_token(
    token_data: Optional[Dict[str, Any]], account_name: str = ""
) -> str:
    """Prefer Google email stored on the token payload, else the node account name."""
    if token_data:
        email = str(token_data.get("email") or "").strip()
        if email:
            return email
    return (account_name or "").strip()


async def hydrate_google_workspace_from_graph() -> int:
    """Hydrate every ``google_workspace`` MCPOAuthToken onto disk. Returns count written."""
    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_database_manager

    from .mcp_oauth_action import hydrate_credential_payload
    from .mcp_oauth_node import MCPOAuthToken

    manager = get_database_manager()
    db = manager.get_database()
    ctx = GraphContext(db)
    nodes = await ctx.find_nodes(
        MCPOAuthToken, {"context.server_name": GOOGLE_WORKSPACE_SERVER}
    )
    written = 0
    for node in nodes or []:
        raw = getattr(node, "token_json", "") or ""
        if not raw:
            continue
        try:
            token_data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "Skipping MCPOAuthToken %s: invalid token_json",
                getattr(node, "id", "?"),
            )
            continue
        if not isinstance(token_data, dict):
            continue
        email = account_email_from_token(
            token_data, getattr(node, "account_name", "") or ""
        )
        payload = hydrate_credential_payload(token_data)
        if not email or not payload.get("refresh_token"):
            continue
        try:
            hydrate_google_workspace_account(email, payload)
            written += 1
        except Exception as exc:
            logger.warning("Failed to hydrate MCP credentials for %s: %s", email, exc)
    return written
