"""Tests for ``CockpitEngine._render_user_identity_block``.

The engine bakes the caller's preferred name into its system prompt so
the model addresses the user without needing to call ``get_user_name``
for every trivial greeting. PersonaAction's ``respond_slim`` mirrors
the same contract for the converse fast-path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.cockpit.context import CockpitContext
from jvagent.action.cockpit.engine import CockpitEngine

pytestmark = pytest.mark.asyncio


def _make_engine(
    *,
    user_id: str | None = "u-1",
    user_node=None,
    memory_raises: bool = False,
    no_agent: bool = False,
) -> CockpitEngine:
    """Build an engine wired to a synthetic ctx for identity-block tests."""
    memory = MagicMock()
    if memory_raises:
        memory.get_user = AsyncMock(side_effect=RuntimeError("db down"))
    else:
        memory.get_user = AsyncMock(return_value=user_node)

    agent = None if no_agent else MagicMock()
    if agent is not None:
        agent.get_memory = AsyncMock(return_value=memory)

    ctx = MagicMock(spec=CockpitContext)
    ctx.user_id = user_id
    ctx.agent = agent

    engine = CockpitEngine.__new__(CockpitEngine)
    engine.ctx = ctx
    return engine


async def test_renders_display_name_when_present() -> None:
    user = MagicMock()
    user.display_name = "Eldon"
    user.name = "eldon.marks"
    engine = _make_engine(user_node=user)

    block = await engine._render_user_identity_block()

    assert "Preferred name: Eldon" in block
    assert "Canonical name: eldon.marks" in block
    assert "Address the user by this name" in block


async def test_renders_canonical_name_when_display_name_missing() -> None:
    user = MagicMock()
    user.display_name = ""
    user.name = "eldon.marks"
    engine = _make_engine(user_node=user)

    block = await engine._render_user_identity_block()

    assert "Preferred name: eldon.marks" in block
    # Canonical line suppressed when both fields would be the same value.
    assert "Canonical name:" not in block


async def test_omits_canonical_when_display_and_name_match() -> None:
    user = MagicMock()
    user.display_name = "Eldon"
    user.name = "Eldon"
    engine = _make_engine(user_node=user)

    block = await engine._render_user_identity_block()

    assert "Preferred name: Eldon" in block
    assert "Canonical name:" not in block


async def test_renders_unknown_stub_when_user_has_no_name() -> None:
    user = MagicMock()
    user.display_name = None
    user.name = None
    engine = _make_engine(user_node=user)

    block = await engine._render_user_identity_block()

    assert "No name is on file" in block
    assert "ask politely" in block
    assert "Never invent a name" in block


async def test_returns_empty_when_user_id_missing() -> None:
    engine = _make_engine(user_id=None)
    assert await engine._render_user_identity_block() == ""


async def test_returns_empty_when_agent_missing() -> None:
    engine = _make_engine(no_agent=True)
    assert await engine._render_user_identity_block() == ""


async def test_returns_empty_when_memory_raises() -> None:
    """Memory subsystem failure must not crash prompt assembly."""
    engine = _make_engine(memory_raises=True)
    assert await engine._render_user_identity_block() == ""


async def test_returns_unknown_stub_when_memory_returns_no_user() -> None:
    """User lookup miss → no-name-on-file branch (not empty string)."""
    engine = _make_engine(user_node=None)
    block = await engine._render_user_identity_block()
    assert "No name is on file" in block
