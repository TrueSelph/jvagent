"""MCP OAuth Action Package for integral-client-ai."""

from . import endpoints  # noqa: F401 - registers endpoints
from .hydrate import (
    hydrate_google_workspace_account,
    hydrate_google_workspace_from_graph,
    mcp_google_workspace_auth_url,
)
from .mcp_oauth_action import MCPOAuthAction
from .mcp_oauth_node import MCPOAuthToken
from .microsoft_hydrate import mcp_microsoft_365_auth_url

__all__ = [
    "MCPOAuthAction",
    "MCPOAuthToken",
    "hydrate_google_workspace_account",
    "hydrate_google_workspace_from_graph",
    "mcp_google_workspace_auth_url",
    "mcp_microsoft_365_auth_url",
]
