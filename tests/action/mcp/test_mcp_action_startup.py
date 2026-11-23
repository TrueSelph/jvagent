"""Regression tests for MCPAction startup lifecycle behavior."""

from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.mcp.mcp_action import MCPAction


@pytest.mark.asyncio
async def test_on_startup_rebuilds_server_registry():
    """Ensure startup repopulates in-memory server entries after rehydrate."""
    action = MCPAction(
        sandbox_mode=False,
        servers=[
            {
                "name": "filesystem",
                "enabled": True,
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
            }
        ],
    )

    # Simulate post-rehydrate state before startup hook executes.
    assert action.get_server_names() == []

    with (
        patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)),
        patch("jvagent.core.app.App.get", new=AsyncMock(return_value=None)),
    ):
        await action.on_startup()

    assert action.get_server_names() == ["filesystem"]


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", ["oauth", "service_key"])
async def test_unsupported_http_auth_profile_fails_before_connect(
    auth_type: str,
) -> None:
    action = MCPAction(
        sandbox_mode=False,
        servers=[
            {
                "name": "external",
                "transport": "streamable_http",
                "url": "https://example.test/mcp",
                "auth": {"type": auth_type},
            }
        ],
    )
    with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
        with pytest.raises(
            ValueError, match="Vendor MCP auth profiles are unsupported"
        ):
            await action._build_server_entries()
    assert action.get_server_names() == []
