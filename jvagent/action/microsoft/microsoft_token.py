from datetime import datetime
from typing import Any, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute
from pydantic import field_validator


class MicrosoftToken(Node):
    """Microsoft / Entra ID OAuth2 token node (delegated permissions)."""

    action_id: str = attribute(
        indexed=True,
        default_factory=str,
        description="ID of the action this token belongs to",
    )
    token: str = attribute(
        default_factory=str,
        description="Access token",
    )
    refresh_token: str = attribute(
        default_factory=str,
        description="Refresh token",
    )
    token_uri: str = attribute(
        default_factory=str,
        description="Token endpoint URL for this tenant",
    )
    client_id: str = attribute(
        default_factory=str,
        description="Application (client) ID",
    )
    client_secret: str = attribute(
        default_factory=str,
        description="Client secret",
    )
    scopes: List[str] = attribute(
        default_factory=list,
        description="Authorized scopes",
    )
    agent_id: str = attribute(
        indexed=True,
        default_factory=str,
        description="ID of the agent this token belongs to",
    )
    expiry: Optional[datetime] = attribute(
        default=None,
        description="Access token expiry",
    )

    @field_validator("expiry", mode="before")
    @classmethod
    def _coerce_expiry(cls, v: Any) -> Optional[datetime]:
        if v is None or v == "":
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v
