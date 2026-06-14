"""Tests for MCPAction multi-server selection and filtering."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.mcp.mcp_action import MCPAction, _parse_tool_selection


def _tool(name: str):
    tool = MagicMock()
    tool.name = name
    tool.description = f"{name} description"
    tool.input_schema = {"type": "object", "properties": {}}
    return tool


def test_strip_trailing_path_keeps_scoped_npm_package():
    """Regression: do not strip @scope/pkg — npx would treat the only path as cwd/package."""
    action = MCPAction(servers=[])
    base = ["-y", "@modelcontextprotocol/server-filesystem"]
    assert action._strip_trailing_path_arg(base) == base
    with_dot = list(base) + ["."]
    assert action._strip_trailing_path_arg(with_dot) == base


class TestMCPActionFiltering:
    @pytest.mark.asyncio
    async def test_filter_tools_all_with_denied_patterns(self):
        action = MCPAction(
            sandbox_mode=False,
            servers=[
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                    "tools": "-all",
                    "denied_tools": ["delete_*"],
                }
            ],
        )
        with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
            await action._build_server_entries()
        entry = action._servers_by_name["filesystem"]

        filtered = action._filter_tools(
            entry, [_tool("read_file"), _tool("delete_file"), _tool("list_files")]
        )
        assert [t.name for t in filtered] == ["read_file", "list_files"]

    @pytest.mark.asyncio
    async def test_filter_tools_allow_list_and_globs(self):
        action = MCPAction(
            sandbox_mode=False,
            servers=[
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                    "tools": ["read_*", "list_files"],
                    "denied_tools": [],
                }
            ],
        )
        with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
            await action._build_server_entries()
        entry = action._servers_by_name["filesystem"]

        filtered = action._filter_tools(
            entry,
            [_tool("read_file"), _tool("list_files"), _tool("write_file")],
        )
        assert [t.name for t in filtered] == ["read_file", "list_files"]


class TestMCPActionFulfill:
    @pytest.mark.asyncio
    async def test_fulfill_selects_server_and_tool(self):
        action = MCPAction(servers=[])
        inventory_tool = _tool("read_file")
        action._resolve_tool_inventory = AsyncMock(
            return_value=[("filesystem", inventory_tool)]
        )

        model_result = MagicMock()
        model_result.get_response = AsyncMock(
            return_value='{"server_name":"filesystem","tool_name":"read_file","arguments":{"path":"README.md"}}'
        )
        model_action = MagicMock()
        model_action.query_sync = AsyncMock(return_value=model_result)

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = "contents"
        call_result = MagicMock()
        call_result.isError = False
        call_result.content = [text_item]
        client = MagicMock()
        client.call_tool = AsyncMock(return_value=call_result)
        with (
            patch.object(
                MCPAction, "get_model_action", new=AsyncMock(return_value=model_action)
            ),
            patch.object(
                MCPAction,
                "get_client_for_user",
                new=AsyncMock(return_value=client),
            ),
        ):
            result = await action.fulfill("read README.md", user_id="user-123")

        assert result.is_error is False
        assert result.text == "contents"
        client.call_tool.assert_awaited_once_with("read_file", {"path": "README.md"})

    @pytest.mark.asyncio
    async def test_fulfill_rejects_invalid_server_tool_pair(self):
        action = MCPAction(servers=[])
        action._resolve_tool_inventory = AsyncMock(
            return_value=[("filesystem", _tool("read_file"))]
        )

        model_result = MagicMock()
        model_result.get_response = AsyncMock(
            return_value='{"server_name":"websearch","tool_name":"search","arguments":{}}'
        )
        model_action = MagicMock()
        model_action.query_sync = AsyncMock(return_value=model_result)
        with patch.object(
            MCPAction, "get_model_action", new=AsyncMock(return_value=model_action)
        ):
            result = await action.fulfill("search the web")

        assert result.is_error is True
        assert result.error_kind == "gateway_error"
        assert "Invalid server/tool selection" in result.text


def test_parse_tool_selection_reads_server_name():
    parsed = _parse_tool_selection(
        '```json\n{"server_name":"filesystem","tool_name":"read_file","arguments":{"path":"a.txt"}}\n```'
    )
    assert parsed == {
        "server_name": "filesystem",
        "tool_name": "read_file",
        "arguments": {"path": "a.txt"},
    }


class TestDefaultPerUserSandboxing:
    """Defaults must auto-scope filesystem MCP under <agent_id>/<user_id>/.

    Regression: prior defaults left ``sandbox_mode`` and
    ``sandbox_user_scoped`` unset (effectively False), so files from every
    user landed in a single shared workspace. The user-facing contract is
    now per-user folders out of the box; only an explicit opt-out should
    disable it.
    """

    def test_attribute_defaults_are_true(self):
        action = MCPAction(servers=[])
        assert action.sandbox_mode is True
        assert action.sandbox_user_scoped is True

    @pytest.mark.asyncio
    async def test_filesystem_server_is_sandbox_mode_by_default(self, tmp_path):
        action = MCPAction(
            servers=[
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                }
            ],
            sandbox_root=str(tmp_path),
        )
        with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
            await action._build_server_entries()

        entry = action._servers_by_name["filesystem"]
        assert entry.sandbox_mode is True
        assert entry.sandbox_user_scoped is True

    @pytest.mark.asyncio
    async def test_get_client_for_user_routes_to_user_subprocess_by_default(
        self, tmp_path
    ):
        """Real user_id triggers a per-user subprocess under <agent_id>/<user_id>/."""
        from jvagent.action.mcp.client import MCPClientWrapper

        action = MCPAction(
            servers=[
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                }
            ],
            sandbox_root=str(tmp_path),
        )
        with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
            await action._build_server_entries()

        entry = action._servers_by_name["filesystem"]
        # Default-user calls reuse the startup client.
        with patch("jvagent.core.app.App.get", new=AsyncMock(return_value=None)):
            default_client = await action.get_client_for_user("filesystem", None)
            assert default_client is entry.client

            user_client = await action.get_client_for_user("filesystem", "user-abc")

        assert isinstance(user_client, MCPClientWrapper)
        assert user_client is not entry.client
        # Same user_id yields the cached client (no duplicate subprocess).
        with patch("jvagent.core.app.App.get", new=AsyncMock(return_value=None)):
            user_client_again = await action.get_client_for_user(
                "filesystem", "user-abc"
            )
        assert user_client_again is user_client

    @pytest.mark.asyncio
    async def test_explicit_opt_out_disables_sandbox(self, tmp_path):
        action = MCPAction(
            sandbox_mode=False,
            sandbox_user_scoped=False,
            servers=[
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                }
            ],
            sandbox_root=str(tmp_path),
        )
        with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
            await action._build_server_entries()

        entry = action._servers_by_name["filesystem"]
        assert entry.sandbox_mode is False
        assert entry.sandbox_user_scoped is False


class TestSanitizeAndUserSegment:
    """Regression: leading underscore preserved; session_id is fallback for anonymous."""

    def test_sanitize_segment_preserves_default_sentinel(self):
        from jvagent.core.sandbox import sanitize_segment

        # Previously stripped leading underscore -> "default"; must preserve.
        assert sanitize_segment("_default") == "_default"

    def test_sanitize_segment_strips_only_dots(self):
        from jvagent.core.sandbox import sanitize_segment

        assert sanitize_segment(".hidden.") == "hidden"
        assert sanitize_segment("..__system__..") == "__system__"

    def test_effective_user_segment_prefers_user_id(self):
        from jvagent.core.sandbox import effective_user_segment

        assert effective_user_segment(user_id="alice", session_id="sess_x") == "alice"

    def test_effective_user_segment_falls_back_to_session_id(self):
        from jvagent.core.sandbox import effective_user_segment

        assert effective_user_segment(user_id=None, session_id="sess_x") == "sess_x"
        assert effective_user_segment(user_id="", session_id="sess_x") == "sess_x"

    def test_effective_user_segment_falls_back_to_default(self):
        from jvagent.core.sandbox import effective_user_segment

        assert effective_user_segment(user_id=None, session_id=None) == "_default"
        assert (
            effective_user_segment(user_id=None, session_id=None, default="anon")
            == "anon"
        )

    def test_resolve_sandbox_relpath_keeps_underscored_default(self):
        from jvagent.core.sandbox import resolve_user_sandbox_relpath

        # Image bug repro: sentinel must render as ``_default`` not ``default``.
        rel = resolve_user_sandbox_relpath("agent_x", "_default")
        assert rel == "agent_x/_default"


class TestFulfillSessionFallback:
    @pytest.mark.asyncio
    async def test_fulfill_uses_session_id_when_user_id_missing(self):
        action = MCPAction(servers=[])
        inventory_tool = _tool("read_file")
        action._resolve_tool_inventory = AsyncMock(
            return_value=[("filesystem", inventory_tool)]
        )

        model_result = MagicMock()
        model_result.get_response = AsyncMock(
            return_value='{"server_name":"filesystem","tool_name":"read_file","arguments":{}}'
        )
        model_action = MagicMock()
        model_action.query_sync = AsyncMock(return_value=model_result)

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = "ok"
        call_result = MagicMock()
        call_result.isError = False
        call_result.content = [text_item]
        client = MagicMock()
        client.call_tool = AsyncMock(return_value=call_result)

        gcfu = AsyncMock(return_value=client)
        with (
            patch.object(
                MCPAction, "get_model_action", new=AsyncMock(return_value=model_action)
            ),
            patch.object(MCPAction, "get_client_for_user", new=gcfu),
        ):
            result = await action.fulfill(
                "read", user_id=None, session_id="sess_anon_42"
            )

        assert result.is_error is False
        # The routing key passed to per-user dispatch should be the session
        # id, ensuring anonymous-but-sessioned callers get their own sandbox
        # folder rather than the shared system-default one.
        gcfu.assert_awaited_once_with("filesystem", "sess_anon_42")


class TestCockpitDispatchRoutesPerUser:
    """Regression: MCP tools dispatched via cockpit's ToolExecutionEngine
    must route to the per-user MCP subprocess (not the default one).

    Scenario from ``zoon_ai_app``: anonymous-but-sessioned visitor wrote
    a file via the MCP filesystem tool. Pre-fix, ``MCPAction.get_tools()``
    closure unconditionally bound ``client = action.get_client(server)``,
    which is the default ``_default`` subprocess. Files always landed in
    ``<agent_id>/_default/`` (rendered as ``default`` before the sanitize
    fix) regardless of the caller. The cockpit dispatcher passes no
    visitor, so the per-user routing in ``_dispatch_mcp_tool`` (skill path)
    never fires here.

    Fix: ``ToolExecutionEngine`` accepts ``visitor=`` and exposes it via a
    ContextVar; ``MCPAction.get_tools()`` reads the CV and calls
    ``get_client_for_user``.
    """

    def _build_action_and_tool_setup(self):
        """Common scaffold: an MCPAction with one mocked filesystem MCP tool.

        Pydantic strict-attribute mode rejects ad-hoc instance overrides, so
        method shims are installed via ``patch.object`` instead of attribute
        assignment.
        """
        action = MCPAction(servers=[])
        mcp_tool = _tool("write_file")
        return action, mcp_tool

    @pytest.mark.asyncio
    async def test_mcp_get_tools_dispatch_uses_per_user_client_via_contextvar(self):
        from types import SimpleNamespace

        from jvagent.tooling.tool_executor import ToolExecutionEngine
        from jvagent.tooling.tool_registry import ToolRegistry

        action, mcp_tool = self._build_action_and_tool_setup()

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = "wrote"
        per_user_call_result = MagicMock()
        per_user_call_result.isError = False
        per_user_call_result.content = [text_item]
        per_user_client = MagicMock()
        per_user_client.call_tool = AsyncMock(return_value=per_user_call_result)

        default_client = MagicMock()
        default_client.call_tool = AsyncMock(
            side_effect=AssertionError(
                "default client must not be called when visitor is in scope"
            )
        )

        gcfu = AsyncMock(return_value=per_user_client)
        with (
            patch.object(
                MCPAction,
                "get_server_names",
                new=lambda self: ["filesystem"],
            ),
            patch.object(
                MCPAction,
                "get_tools_cached",
                new=AsyncMock(return_value=[mcp_tool]),
            ),
            patch.object(MCPAction, "get_client", new=lambda self, sn: default_client),
            patch.object(MCPAction, "get_client_for_user", new=gcfu),
        ):
            tools = await action.get_tools()
            assert len(tools) == 1
            tool = tools[0]

            registry = ToolRegistry()
            registry.register(tool, prefix="action")

            visitor = SimpleNamespace(user_id="alice", session_id="sess_abc")
            engine = ToolExecutionEngine(registry, visitor=visitor)
            tool_calls = [
                {
                    "id": "call_1",
                    "function": {
                        "name": tool.name,
                        "arguments": '{"path":"x.md","content":"hi"}',
                    },
                }
            ]
            results = await engine.dispatch(tool_calls)

        assert len(results) == 1
        assert results[0].is_error is False
        gcfu.assert_awaited_once_with("filesystem", "alice")
        per_user_client.call_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mcp_get_tools_dispatch_falls_back_to_session_id(self):
        """Anonymous visitor (no user_id) should route to session_id sandbox."""
        from types import SimpleNamespace

        from jvagent.tooling.tool_executor import ToolExecutionEngine
        from jvagent.tooling.tool_registry import ToolRegistry

        action, mcp_tool = self._build_action_and_tool_setup()

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = "wrote"
        cr = MagicMock()
        cr.isError = False
        cr.content = [text_item]
        per_user_client = MagicMock()
        per_user_client.call_tool = AsyncMock(return_value=cr)

        gcfu = AsyncMock(return_value=per_user_client)
        with (
            patch.object(
                MCPAction,
                "get_server_names",
                new=lambda self: ["filesystem"],
            ),
            patch.object(
                MCPAction,
                "get_tools_cached",
                new=AsyncMock(return_value=[mcp_tool]),
            ),
            patch.object(MCPAction, "get_client", new=lambda self, sn: MagicMock()),
            patch.object(MCPAction, "get_client_for_user", new=gcfu),
        ):
            tools = await action.get_tools()
            registry = ToolRegistry()
            registry.register(tools[0], prefix="action")

            visitor = SimpleNamespace(user_id=None, session_id="sess_xyz")
            engine = ToolExecutionEngine(registry, visitor=visitor)
            await engine.dispatch(
                [
                    {
                        "id": "c2",
                        "function": {"name": tools[0].name, "arguments": "{}"},
                    }
                ]
            )

        gcfu.assert_awaited_once_with("filesystem", "sess_xyz")

    @pytest.mark.asyncio
    async def test_mcp_get_tools_dispatch_uses_default_client_without_visitor(
        self,
    ):
        """No visitor in scope (raw scripted run) keeps the default client."""
        from jvagent.tooling.tool_executor import ToolExecutionEngine
        from jvagent.tooling.tool_registry import ToolRegistry

        action, mcp_tool = self._build_action_and_tool_setup()

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = "wrote"
        cr = MagicMock()
        cr.isError = False
        cr.content = [text_item]
        default_client = MagicMock()
        default_client.call_tool = AsyncMock(return_value=cr)

        gcfu = AsyncMock(
            side_effect=AssertionError(
                "get_client_for_user should NOT be called without a visitor"
            )
        )
        with (
            patch.object(
                MCPAction,
                "get_server_names",
                new=lambda self: ["filesystem"],
            ),
            patch.object(
                MCPAction,
                "get_tools_cached",
                new=AsyncMock(return_value=[mcp_tool]),
            ),
            patch.object(MCPAction, "get_client", new=lambda self, sn: default_client),
            patch.object(MCPAction, "get_client_for_user", new=gcfu),
        ):
            tools = await action.get_tools()
            registry = ToolRegistry()
            registry.register(tools[0], prefix="action")

            engine = ToolExecutionEngine(registry)  # no visitor
            await engine.dispatch(
                [
                    {
                        "id": "c3",
                        "function": {"name": tools[0].name, "arguments": "{}"},
                    }
                ]
            )

        default_client.call_tool.assert_awaited_once()
