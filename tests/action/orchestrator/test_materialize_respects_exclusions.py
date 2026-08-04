"""Late skill-tool materialization must respect the assembly's exclusions.

``ensure_skill_tools_materialized`` re-adds tools a skill declares in
``requires_tools``/``allowed-tools`` that are missing from the turn's surface —
the legitimate case is a tool a bound action *pruned* while its runtime warmed
up. Re-deriving that surface from raw ``get_tools()`` output, however, also
resurrects tools ``_assemble_tools`` excluded on purpose:

1. ``denied_tools`` — documented as "not listed, not find_tool-reachable, not
   dispatchable".
2. MCP tools from a server excluded by ``tool_servers``.
3. AccessControl labels on MCP tools (``tool:delegate:{name}``), lost when the
   tool is re-wrapped without one.

These are regressions of the exclusion contract, exercised through the
production entry points (``_apply_active_task_lock_skill`` →
``apply_task_lock_turn``, and ``_apply_unlocked_skill_surface``).
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List, Set, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult


class _PlainToolsAction:
    """A non-flow action publishing hand-built capability tools."""

    enabled = True

    def __init__(self, names: List[str]) -> None:
        self._tools = [
            Tool(
                name=n,
                description=f"Capability {n}.",
                parameters_schema={"type": "object", "properties": {}},
                execute=self._make_exec(n),
            )
            for n in names
        ]

    @staticmethod
    def _make_exec(name: str):
        async def _exec(**_kwargs: Any) -> ToolResult:
            return ToolResult(content=f"ran {name}")

        return _exec

    def get_class_name(self) -> str:
        return type(self).__name__

    async def get_tools(self) -> List[Tool]:
        return list(self._tools)


@pytest.fixture
def fake_mcp_base(monkeypatch):
    """Inject a stub ``MCPAction`` class so isinstance checks match test doubles.

    ``_mcp_action_class`` / ``_select_mcp_actions`` import the real module
    lazily; these tests must not depend on the ``mcp`` extra being installed.
    """

    class _MCPBase:
        pass

    module = types.ModuleType("jvagent.action.mcp.mcp_action")
    module.MCPAction = _MCPBase  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jvagent.action.mcp.mcp_action", module)
    return _MCPBase


def _mcp_action_cls(base: type):
    class _McpServer(base):  # type: ignore[misc, valid-type]
        enabled = True

        def __init__(self, server_name: str, tool_names: List[str]) -> None:
            self._server_name = server_name
            self._tools = [
                Tool(
                    name=n,
                    description=f"MCP capability {n}.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=self._make_exec(n),
                )
                for n in tool_names
            ]

        @staticmethod
        def _make_exec(name: str):
            async def _exec(**_kwargs: Any) -> ToolResult:
                return ToolResult(content=f"ran {name}")

            return _exec

        def get_class_name(self) -> str:
            return self._server_name

        async def get_tools(self) -> List[Tool]:
            return list(self._tools)

    return _McpServer


def _locked_skill(name: str, required: Tuple[str, ...]) -> SkillDoc:
    return SkillDoc(
        name=name,
        description="d",
        body="SOP body",
        requires_tools=required,
        task_lock=True,
    )


async def _assemble(ex: Any, visitor: Any) -> Tuple[Dict[str, Any], Set[str]]:
    visible: Set[str] = set()
    tools = await ex._assemble_tools(
        visitor, [], visible, None, visitor.utterance, [], {}
    )
    return tools, visible


# --- 1. denied_tools survives late materialization --------------------------


async def test_task_lock_skill_cannot_resurrect_a_denied_tool(
    make_orchestrator, make_visitor
):
    action = _PlainToolsAction(["secrets__list", "weather__current"])
    ex = make_orchestrator(actions=[action])
    ex.denied_tools = ["secrets__*"]
    visitor = make_visitor(utterance="hi")

    tools, visible = await _assemble(ex, visitor)
    assert "secrets__list" not in tools  # assembly excluded it

    skill = _locked_skill("leaky_skill", ("secrets__list",))
    out_tools, out_visible, _section = await ex._apply_active_task_lock_skill(
        skill, [action], visitor, "hi", tools, visible, [], []
    )

    assert "secrets__list" not in out_tools
    assert "secrets__list" not in out_visible


async def test_unlocked_skill_surface_cannot_resurrect_a_denied_tool(
    make_orchestrator, make_visitor
):
    action = _PlainToolsAction(["secrets__list"])
    ex = make_orchestrator(actions=[action])
    ex.denied_tools = ["secrets__list"]
    visitor = make_visitor(utterance="hi")

    tools, visible = await _assemble(ex, visitor)
    assert "secrets__list" not in tools

    doc = SkillDoc(
        name="plain_skill",
        description="d",
        body="SOP",
        requires_tools=("secrets__list",),
    )
    out_tools, out_visible, _section = await ex._apply_unlocked_skill_surface(
        doc, [action], visitor, tools, visible, []
    )

    assert "secrets__list" not in out_tools
    assert "secrets__list" not in out_visible


# --- 2. the tool_servers MCP gate survives late materialization -------------


async def test_task_lock_skill_cannot_resurrect_a_non_selected_mcp_tool(
    make_orchestrator, make_visitor, fake_mcp_base
):
    server_cls = _mcp_action_cls(fake_mcp_base)
    selected = server_cls("SelectedServer", ["mcp_selected__ping"])
    excluded = server_cls("ExcludedServer", ["mcp_excluded__ping"])
    ex = make_orchestrator(actions=[selected, excluded])
    ex.tool_servers = ["SelectedServer"]
    visitor = make_visitor(utterance="hi")

    tools, visible = await _assemble(ex, visitor)
    assert "mcp_selected__ping" in tools
    assert "mcp_excluded__ping" not in tools  # gated out by tool_servers

    skill = _locked_skill("leaky_skill", ("mcp_excluded__ping",))
    out_tools, out_visible, _section = await ex._apply_active_task_lock_skill(
        skill, [selected, excluded], visitor, "hi", tools, visible, [], []
    )

    assert "mcp_excluded__ping" not in out_tools
    assert "mcp_excluded__ping" not in out_visible


# --- 3. AccessControl label survives late materialization -------------------


async def test_materialized_mcp_tool_keeps_its_access_control_label(
    make_orchestrator, make_visitor, fake_mcp_base
):
    server_cls = _mcp_action_cls(fake_mcp_base)
    server = server_cls("SelectedServer", ["mcp_selected__ping"])

    access_control = MagicMock()
    access_control.policy_applies = MagicMock(return_value=True)
    access_control.has_action_access = AsyncMock(return_value=False)
    agent = MagicMock()
    agent.get_access_control_action = AsyncMock(return_value=access_control)

    ex = make_orchestrator(actions=[server], agent=agent)
    ex.tool_servers = "-all"
    visitor = make_visitor(utterance="hi")

    tools, visible = await _assemble(ex, visitor)
    assert await tools["mcp_selected__ping"].run({}) == "(access denied)"

    # A bound action pruned the tool while its runtime warmed up — the exact
    # case materialization exists for.
    tools.pop("mcp_selected__ping")
    visible.discard("mcp_selected__ping")

    skill = _locked_skill("mcp_skill", ("mcp_selected__ping",))
    out_tools, _out_visible, _section = await ex._apply_active_task_lock_skill(
        skill, [server], visitor, "hi", tools, visible, [], []
    )

    assert "mcp_selected__ping" in out_tools  # re-added (the legitimate case)
    assert await out_tools["mcp_selected__ping"].run({}) == "(access denied)"


# --- the legitimate case still works ---------------------------------------


async def test_pruned_action_tool_is_still_materialized(
    make_orchestrator, make_visitor
):
    action = _PlainToolsAction(["weather__current"])
    ex = make_orchestrator(actions=[action])
    visitor = make_visitor(utterance="hi")

    tools, visible = await _assemble(ex, visitor)
    tools.pop("weather__current")
    visible.discard("weather__current")

    skill = _locked_skill("weather_skill", ("weather__current",))
    out_tools, out_visible, _section = await ex._apply_active_task_lock_skill(
        skill, [action], visitor, "hi", tools, visible, [], []
    )

    assert "weather__current" in out_tools
    assert "weather__current" in out_visible
    assert await out_tools["weather__current"].run({}) == "ran weather__current"
