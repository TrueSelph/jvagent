"""Tests for jvagent.tooling.tool_decorator (the @tool decorator + collector)."""

from __future__ import annotations

from typing import Annotated

import pytest

from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_decorator import TOOL_MARKER, ToolSpec, collect_tools, tool


class FakeAction:
    """Minimal stand-in exposing the attributes collect_tools reads."""

    metadata = {"name": "myact"}
    label = None

    @tool
    async def greet(self, who: Annotated[str, "who to greet"]) -> str:
        """Greet someone by name.

        Extra detail that must NOT appear in the tool description.
        """
        return f"hi {who}"

    @tool(name="explicit_name")
    async def renamed(self) -> str:
        """Has an explicit name."""
        return "ok"

    @tool(access_label="secret", terminal=True, binds_visitor=True)
    async def _hidden(self) -> str:
        """Underscore-named but still collected."""
        return "shh"

    async def not_a_tool(self) -> str:
        """Undecorated — must be ignored."""
        return "nope"


class NoMetaAction:
    metadata = None
    label = None

    @tool
    async def ping(self) -> str:
        """Ping."""
        return "pong"


def test_decorator_attaches_marker():
    spec = getattr(FakeAction.greet, TOOL_MARKER, None)
    assert isinstance(spec, ToolSpec)


def test_decorator_leaves_function_callable():
    # @tool must not wrap/replace the function.
    assert callable(FakeAction.greet)


def _by_name(tools):
    return {t.name: t for t in tools}


def test_name_derivation_from_metadata():
    tools = _by_name(collect_tools(FakeAction()))
    assert "myact__greet" in tools


def test_explicit_name_override():
    tools = _by_name(collect_tools(FakeAction()))
    assert "explicit_name" in tools
    assert "myact__renamed" not in tools


def test_underscore_method_collected_with_hints():
    tools = _by_name(collect_tools(FakeAction()))
    assert "myact___hidden" in tools
    hidden = tools["myact___hidden"]
    assert hidden.access_label == "secret"
    assert hidden.terminal is True
    assert hidden.binds_visitor is True


def test_undecorated_method_ignored():
    tools = _by_name(collect_tools(FakeAction()))
    assert all("not_a_tool" not in n for n in tools)


def test_description_is_first_paragraph_only():
    tools = _by_name(collect_tools(FakeAction()))
    assert tools["myact__greet"].description == "Greet someone by name."


def test_schema_derived_from_signature():
    tools = _by_name(collect_tools(FakeAction()))
    schema = tools["myact__greet"].parameters_schema
    assert schema["required"] == ["who"]
    assert schema["properties"]["who"]["description"] == "who to greet"


def test_name_fallback_from_class_name():
    tools = _by_name(collect_tools(NoMetaAction()))
    # NoMetaAction -> strip "Action" -> NoMeta -> no_meta
    assert "no_meta__ping" in tools


@pytest.mark.asyncio
async def test_built_tool_calls_through_to_bound_method():
    tools = _by_name(collect_tools(FakeAction()))
    result = await tools["myact__greet"].call(who="ada")
    assert result.content == "hi ada"


def test_returns_empty_when_nothing_decorated():
    class Plain:
        metadata = {"name": "plain"}
        label = None

    assert collect_tools(Plain()) == []


def test_subclass_override_shadows_base():
    class Base:
        metadata = {"name": "x"}
        label = None

        @tool
        async def act(self) -> str:
            """Base."""
            return "base"

    class Sub(Base):
        @tool
        async def act(self) -> str:
            """Sub."""
            return "sub"

    tools = collect_tools(Sub())
    assert len(tools) == 1
    assert tools[0].description == "Sub."


@pytest.mark.asyncio
async def test_web_fetch_uses_base_default():
    # Integration: web_fetch no longer overrides get_tools; the base default
    # (collect_tools) must surface the decorated fetch with a stable name.
    from jvagent.action.web_fetch.web_fetch_action import WebFetchAction

    tools = await WebFetchAction().get_tools()
    assert [t.name for t in tools] == ["web_fetch__fetch"]
    assert isinstance(tools[0], Tool)
    assert tools[0].parameters_schema["required"] == ["url"]
