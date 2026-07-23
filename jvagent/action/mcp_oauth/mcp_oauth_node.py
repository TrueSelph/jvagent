"""Graph node storing MCP server OAuth credentials and refresh tokens."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from jvspatial.core import Node
from jvspatial.core.annotations import attribute


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MCPOAuthToken(Node):
    """Stores encrypted OAuth credentials for an MCP account.

    Ensures serverless / ephemeral environments can persist refresh tokens
    across invocations. ``token_json`` is ciphertext (see oauth.token_crypto);
    never log its contents.
    """

    server_name: str = attribute(
        indexed=True,
        default="",
        description="Name of the MCP server (e.g., google_workspace).",
    )

    account_name: str = attribute(
        indexed=True,
        default="default",
        description="Account identifier used within the MCP server config.",
    )

    token_json: str = attribute(
        default="",
        description="Encrypted JSON of refresh_token / client_id (no client_secret).",
    )

    created: datetime = attribute(
        default_factory=_utc_now,
        description="UTC timestamp when the credentials were first saved.",
    )

    updated: datetime = attribute(
        default_factory=_utc_now,
        description="UTC timestamp when the credentials were last refreshed or updated.",
    )

    def to_api_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict for REST responses (no secrets)."""
        return {
            "id": getattr(self, "id", None),
            "server_name": self.server_name,
            "account_name": self.account_name,
            "created": self.created.isoformat() if self.created else "",
            "updated": self.updated.isoformat() if self.updated else "",
        }
