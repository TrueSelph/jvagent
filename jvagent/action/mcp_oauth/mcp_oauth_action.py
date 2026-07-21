"""Action for managing no-code client OAuth flows for stdio MCP servers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


class MCPOAuthAction(Action):
    """Action that coordinates saving, loading, and refreshing OAuth tokens for MCP servers.

    This operates alongside jvagent/mcp to provide browser-based OAuth authorization
    mechanisms for stdio subprocess servers (like google-workspace-mcp).
    """

    redirect_uri: str = attribute(
        default="",
        description="The authorized redirect URI to configure in your Google Cloud Console.",
    )
    auth_url: str = attribute(
        default="",
        description="The link to visit in your browser to authorize your Google Workspace accounts.",
    )

    # Endpoints prefix to tell FastAPI to mount these routes
    mcp_oauth_endpoint_path_prefixes: List[str] = [
        "/api/mcp/{server_name}/auth",
        "/api/mcp/{server_name}/auth/callback",
    ]

    async def _apply_env_defaults(self) -> None:
        base = get_public_base_url()
        if base:
            base_clean = base.rstrip("/")

            # Discover all configured MCP server names
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
                # Only offer OAuth endpoints for actual configured servers
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
