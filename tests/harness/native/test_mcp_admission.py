"""Unqualified MCP profiles and missing reviewed tools never reach a model."""

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pydantic_ai_harness")
pytest.importorskip("fastmcp.client")
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.mcp.mcp_action import MCPAction
from jvagent.harness.native import build_native_agent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"transport": "streamable_http", "url": "https://example.test/mcp"},
        {"show_tools": False},
        {"denied_tools": ["lookup_record"]},
    ],
)
async def test_unqualified_server_profiles_reject(overrides: dict[str, Any]) -> None:
    action = MCPAction(
        sandbox_mode=False,
        native_read_tools=["mcp_records__lookup_record"],
        servers=[
            {
                "name": "records",
                "transport": "stdio",
                "command": sys.executable,
                **overrides,
            }
        ],
    )
    with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="native_read_tools|not qualified"):
            await action.get_native_toolsets()


@pytest.mark.asyncio
async def test_missing_remote_tool_fails_before_model_and_closes_server(
    tmp_path: Path,
) -> None:
    action = MCPAction(
        sandbox_mode=False,
        native_read_tools=["mcp_records__missing"],
        servers=[
            {
                "name": "records",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(Path(__file__).with_name("mcp_fixture.py"))],
                "env": {"JV_MCP_FIXTURE_PID": str(tmp_path / "server.pid")},
            }
        ],
    )
    directory = tmp_path / "read"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: read\ndescription: Read\nallowed-tools: [mcp_records__missing]\n---\nRead.\n"
    )
    calls = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        calls.append(True)
        return ModelResponse(parts=[TextPart("unexpected")])

    with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
        toolsets = await action.get_native_toolsets()
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[],
        toolsets=toolsets,
        access_check=AsyncMock(return_value=True),
        instructions="Read.",
    )
    with pytest.raises(ValueError, match="native_read_tools"):
        await agent.run("Read.")
    assert not calls
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "server.pid").read_text()), 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reviewed",
    [
        ["mcp_records__*"],
        ["mcp_records__lookup_record", "mcp_records__lookup_record"],
        ["mcp_absent__read"],
    ],
)
async def test_invalid_read_declarations_reject(reviewed: list[str]) -> None:
    action = MCPAction(
        servers=[{"name": "records", "transport": "stdio", "command": sys.executable}],
        native_read_tools=reviewed,
    )
    with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="native_read_tools"):
            await action.get_native_toolsets()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_native_mcp_requires_unchanged_host_context(changed: bool) -> None:
    from types import SimpleNamespace

    from jvagent.harness.mcp import ReviewedMCPToolset
    from jvagent.tooling.context import bind_dispatch_context

    wrapped = SimpleNamespace(call_tool=AsyncMock())
    toolset = ReviewedMCPToolset(
        wrapped, frozenset({"read"}), frozenset({"read"}), AsyncMock(return_value=True)
    )
    visitor = SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id="alice",
        session_id="session",
        interaction=SimpleNamespace(id="turn", user_id="alice", session_id="session"),
    )
    with bind_dispatch_context(visitor if changed else None):
        if changed:
            visitor.user_id = "bob"
        with pytest.raises(ValueError, match="host context|scope changed"):
            await toolset.call_tool("read", {}, None, None)
    wrapped.call_tool.assert_not_awaited()
