"""Action for managing no-code client OAuth flows for stdio MCP servers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_database_manager

from jvagent.action.base import Action
from jvagent.action.oauth.token_crypto import (
    decrypt_token_from_storage,
    encrypt_token_for_storage,
)
from jvagent.core.public_url import get_public_base_url

from .mcp_oauth_node import MCPOAuthToken

logger = logging.getLogger(__name__)


async def _get_ctx() -> GraphContext:
    """Get a GraphContext for the database."""
    manager = get_database_manager()
    db = manager.get_database()
    return GraphContext(db)


def _serialize_token_payload(token_data: Dict[str, Any]) -> str:
    """Encrypt token JSON for at-rest storage.

    Never persist ``client_secret`` — callers resolve it from
    ``GOOGLE_CLIENT_SECRETS_JSON`` at use time.
    """
    safe = {k: v for k, v in token_data.items() if k != "client_secret"}
    return encrypt_token_for_storage(json.dumps(safe))


def _deserialize_token_payload(stored: str) -> Optional[Dict[str, Any]]:
    """Decrypt or accept legacy plaintext JSON token blobs."""
    if not stored:
        return None
    plain = decrypt_token_from_storage(stored)
    if not plain:
        # decrypt returns empty on hard failure; try legacy plaintext JSON
        try:
            parsed = json.loads(stored)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            return None
        return None
    try:
        parsed = json.loads(plain)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("Failed to parse stored token_json: %s", exc)
        return None


class MCPOAuthAction(Action):
    """Coordinates browser OAuth for stdio MCP servers (e.g. google-workspace-mcp)."""

    redirect_uri: str = attribute(
        default="",
        description="The authorized redirect URI to configure in your Google Cloud Console.",
    )
    auth_url: str = attribute(
        default="",
        description="The link to visit in your browser to authorize your Google Workspace accounts.",
    )

    # Extra routes live under /mcp/... (not /actions/{id}/); declare for deregister.
    additional_endpoint_path_prefixes: ClassVar[List[str]] = ["/mcp/"]

    async def _apply_env_defaults(self) -> None:
        base = get_public_base_url()
        if base:
            base_clean = base.rstrip("/")

            try:
                ctx = await _get_ctx()
                from jvagent.action.mcp.mcp_action import MCPAction

                nodes = await ctx.find_nodes(MCPAction, {})
                mcp_action = nodes[0] if nodes else None
                server_names = (
                    mcp_action.get_server_names()
                    if mcp_action
                    else ["google_workspace"]
                )
            except Exception:
                server_names = ["google_workspace"]

            if not server_names:
                server_names = ["google_workspace"]

            auth_urls = []
            redirect_uris = []
            for name in server_names:
                auth_urls.append(
                    f"{name}: {base_clean}/api/mcp/{name}/auth?account=integral"
                )
                redirect_uris.append(
                    f"{name}: {base_clean}/api/mcp/{name}/auth/callback"
                )

            self.auth_url = "\n".join(auth_urls)
            self.redirect_uri = "\n".join(redirect_uris)
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
        """Create or update an encrypted token node in the graph database."""
        ctx = await _get_ctx()
        now = datetime.now(timezone.utc)
        encrypted = _serialize_token_payload(token_data)

        filters = {
            "context.server_name": server_name,
            "context.account_name": account_name,
        }
        nodes = await ctx.find_nodes(MCPOAuthToken, filters)

        if nodes:
            node = nodes[0]
            node.token_json = encrypted
            node.updated = now
            logger.info(
                "Updating existing MCPOAuthToken for %s/%s", server_name, account_name
            )
        else:
            node = MCPOAuthToken(
                server_name=server_name,
                account_name=account_name,
                token_json=encrypted,
                created=now,
                updated=now,
            )
            await node.set_context(ctx)
            logger.info(
                "Creating new MCPOAuthToken for %s/%s", server_name, account_name
            )

        await node.save()

        from jvagent.core.app import App

        app = await App.get()
        if app:
            await app.connect(node)

    async def get_oauth_token(
        self,
        server_name: str,
        account_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch decrypted token data for a server/account pairing."""
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

        return _deserialize_token_payload(node.token_json)
