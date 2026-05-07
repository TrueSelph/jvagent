"""Cockpit memory_set / memory_append tolerate the ``value`` alias.

The model frequently mirrors ``memory_set_preference``'s ``value`` parameter
when calling ``memory_set``; previously this raised ``TypeError`` and the
sanitize layer surfaced the failure as ``Tool execution failed: memory_set``.
Both writers now accept ``value`` as an alias for ``content``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.cockpit.tools.memory import _build_memory_tools


def _unwrap(result) -> str:
    return (
        result if isinstance(result, str) else getattr(result, "content", str(result))
    )


def _wire_user_scope(cockpit_ctx) -> MagicMock:
    """Configure a User node accessible via ``ctx.agent.get_memory().get_user()``."""
    user_node = MagicMock()
    user_node.memory = {}
    user_node.memory_tags = {}
    user_node.save = AsyncMock()
    memory = MagicMock()
    memory.get_user = AsyncMock(return_value=user_node)
    cockpit_ctx.agent = MagicMock()
    cockpit_ctx.agent.get_memory = AsyncMock(return_value=memory)
    cockpit_ctx.user_id = "u-1"
    return user_node


@pytest.mark.asyncio
async def test_memory_set_accepts_value_alias(cockpit_ctx):
    user_node = _wire_user_scope(cockpit_ctx)
    tools = _build_memory_tools(cockpit_ctx)
    memory_set = next(t for t in tools if t.name == "memory_set")

    result = _unwrap(await memory_set.call(key="name", value="Eldon"))
    assert "Memory set" in result
    assert user_node.memory["name"] == "Eldon"
    user_node.save.assert_awaited()


@pytest.mark.asyncio
async def test_memory_set_prefers_content_when_both_supplied(cockpit_ctx):
    user_node = _wire_user_scope(cockpit_ctx)
    tools = _build_memory_tools(cockpit_ctx)
    memory_set = next(t for t in tools if t.name == "memory_set")

    await memory_set.call(key="name", content="canonical", value="alias-loses")
    assert user_node.memory["name"] == "canonical"


@pytest.mark.asyncio
async def test_memory_set_rejects_missing_body(cockpit_ctx):
    _wire_user_scope(cockpit_ctx)
    tools = _build_memory_tools(cockpit_ctx)
    memory_set = next(t for t in tools if t.name == "memory_set")

    result = _unwrap(await memory_set.call(key="name"))
    assert "content" in result.lower() and "required" in result.lower()


@pytest.mark.asyncio
async def test_memory_set_swallows_unexpected_kwargs(cockpit_ctx):
    """A model that hallucinates extra kwargs must not crash the tool."""
    _wire_user_scope(cockpit_ctx)
    tools = _build_memory_tools(cockpit_ctx)
    memory_set = next(t for t in tools if t.name == "memory_set")

    result = _unwrap(
        await memory_set.call(
            key="name", content="Eldon", confidence=0.9, source="user_message"
        )
    )
    assert "Memory set" in result


@pytest.mark.asyncio
async def test_memory_append_accepts_value_alias(cockpit_ctx):
    user_node = _wire_user_scope(cockpit_ctx)
    user_node.memory = {"journal": "previous entry"}
    tools = _build_memory_tools(cockpit_ctx)
    memory_append = next(t for t in tools if t.name == "memory_append")

    result = _unwrap(await memory_append.call(key="journal", value="next entry"))
    assert "Memory appended" in result
    assert "next entry" in user_node.memory["journal"]
    assert "previous entry" in user_node.memory["journal"]
