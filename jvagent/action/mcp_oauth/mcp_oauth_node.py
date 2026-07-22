"""Graph node storing MCP server OAuth credentials and refresh tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from jvspatial.core import Node
from jvspatial.core.annotations import attribute


class MCPOAuthToken(Node):
    """Stores the OAuth credentials (access/refresh tokens) for an MCP account.

    This ensures that in serverless or ephemeral environments (e.g., AWS Lambda),
    the OAuth tokens persist across invocations, and the wrapper script can
    pull them and write them to the local filesystem before booting the MCP server.
    """

    server_name: str = attribute(
        indexed=True,
        default="",
        description="Name of the MCP server (e.g., google_workspace).",
    )

    account_name: str = attribute(
        indexed=True,
        default="default",
        description="Account identifier used within the MCP server config (e.g., integral).",
    )

    token_json: str = attribute(
        default="",
        description="Serialized JSON containing access_token, refresh_token, client_id, etc.",
    )

    created: datetime = attribute(
        default_factory=datetime.utcnow,
        description="UTC timestamp when the credentials were first saved.",
    )

    updated: datetime = attribute(
        default_factory=datetime.utcnow,
        description="UTC timestamp when the credentials were last refreshed or updated.",
    )

    def to_api_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict for REST responses."""
        return {
            "id": getattr(self, "id", None),
            "server_name": self.server_name,
            "account_name": self.account_name,
            "created": self.created.isoformat() if self.created else "",
            "updated": self.updated.isoformat() if self.updated else "",
        }
