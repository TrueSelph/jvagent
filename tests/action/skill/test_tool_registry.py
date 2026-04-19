"""Tests for thinking tool registry."""

from jvagent.action.skill.tool_registry import ToolRegistry


def _noop_dispatch(*args, **kwargs):
    return None


def test_tool_registry_registers_basic_handle():
    registry = ToolRegistry()
    handle = registry.register(
        name="read_file",
        source="mcp",
        schema={"type": "object"},
        dispatch=_noop_dispatch,
        fq_name="mcp:fs:read_file",
    )
    assert handle.name == "read_file"
    assert handle.fq_name == "mcp:fs:read_file"
    assert registry.get("read_file") is not None


def test_tool_registry_namespaces_collisions_with_prefix():
    registry = ToolRegistry()
    registry.register(
        name="read_file",
        source="mcp",
        schema={"type": "object"},
        dispatch=_noop_dispatch,
        fq_name="mcp:fs:read_file",
    )
    handle = registry.register(
        name="read_file",
        source="local",
        schema={"type": "object"},
        dispatch=_noop_dispatch,
        fq_name="local:read_file",
        prefix="local",
    )
    assert handle.name == "local__read_file"
    assert registry.get("local__read_file") is not None
