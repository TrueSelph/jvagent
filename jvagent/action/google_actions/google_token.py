from datetime import datetime
from typing import Any, Dict, List

from jvspatial.core import Node
from jvspatial.core.annotations import attribute


class GoogleToken(Node):
    """Google OAuth2 token node."""

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
        description="Token URI",
    )
    client_id: str = attribute(
        default_factory=str,
        description="Client ID",
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

    expiry: datetime = attribute(
        default_factory=datetime,
        description="Token expiry timestamp",
    )
