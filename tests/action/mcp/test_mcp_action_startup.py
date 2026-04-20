"""Regression tests for MCPAction startup lifecycle behavior."""

import pytest

from jvagent.action.mcp.mcp_action import MCPAction


@pytest.mark.asyncio
async def test_on_startup_rebuilds_server_registry():
    """Ensure startup repopulates in-memory server entries after rehydrate."""
    action = MCPAction(
        servers=[
            {
                "name": "filesystem",
                "enabled": True,
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
            }
        ]
    )

    # Simulate post-rehydrate state before startup hook executes.
    assert action.get_server_names() == []

    await action.on_startup()

    assert action.get_server_names() == ["filesystem"]
