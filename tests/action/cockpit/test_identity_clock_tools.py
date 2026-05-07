"""Cockpit clock + identity harness tools."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.cockpit.tools.clock import _build_clock_tools
from jvagent.action.cockpit.tools.identity import _build_identity_tools


def _unwrap(result) -> str:
    return (
        result if isinstance(result, str) else getattr(result, "content", str(result))
    )


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_datetime_returns_iso_with_weekday_and_tz(cockpit_ctx):
    fixed = datetime(2026, 5, 6, 14, 30, tzinfo=timezone.utc)

    fake_app = MagicMock()
    fake_app.now = AsyncMock(return_value=fixed)
    with patch(
        "jvagent.core.app.App.get",
        new=AsyncMock(return_value=fake_app),
    ):
        tools = _build_clock_tools(cockpit_ctx)
        get_dt = next(t for t in tools if t.name == "get_current_datetime")
        text = _unwrap(await get_dt.call())

    assert "2026-05-06T14:30" in text
    assert "weekday=Wednesday" in text
    assert "timezone=" in text


@pytest.mark.asyncio
async def test_get_current_datetime_falls_back_to_utc_without_app(cockpit_ctx):
    with patch(
        "jvagent.core.app.App.get",
        new=AsyncMock(return_value=None),
    ):
        tools = _build_clock_tools(cockpit_ctx)
        get_dt = next(t for t in tools if t.name == "get_current_datetime")
        text = _unwrap(await get_dt.call())

    # ISO output with timezone field — must not crash without an App.
    assert "T" in text and "timezone=" in text


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_name_prefers_display_name(cockpit_ctx):
    user_node = MagicMock()
    user_node.display_name = "Eldon"
    user_node.name = "eldon.marks"
    memory = MagicMock()
    memory.get_user = AsyncMock(return_value=user_node)
    cockpit_ctx.agent = MagicMock()
    cockpit_ctx.agent.get_memory = AsyncMock(return_value=memory)
    cockpit_ctx.user_id = "u-1"

    tools = _build_identity_tools(cockpit_ctx)
    get_name = next(t for t in tools if t.name == "get_user_name")
    assert _unwrap(await get_name.call()) == "Eldon"


@pytest.mark.asyncio
async def test_get_user_name_falls_back_to_name(cockpit_ctx):
    user_node = MagicMock()
    user_node.display_name = None
    user_node.name = "eldon.marks"
    memory = MagicMock()
    memory.get_user = AsyncMock(return_value=user_node)
    cockpit_ctx.agent = MagicMock()
    cockpit_ctx.agent.get_memory = AsyncMock(return_value=memory)
    cockpit_ctx.user_id = "u-1"

    tools = _build_identity_tools(cockpit_ctx)
    get_name = next(t for t in tools if t.name == "get_user_name")
    assert _unwrap(await get_name.call()) == "eldon.marks"


@pytest.mark.asyncio
async def test_get_user_name_returns_unknown_without_user_id(cockpit_ctx):
    cockpit_ctx.user_id = None
    tools = _build_identity_tools(cockpit_ctx)
    get_name = next(t for t in tools if t.name == "get_user_name")
    text = _unwrap(await get_name.call())
    assert "unknown" in text


@pytest.mark.asyncio
async def test_get_user_name_returns_unknown_when_user_node_has_no_name(cockpit_ctx):
    user_node = MagicMock()
    user_node.display_name = None
    user_node.name = None
    memory = MagicMock()
    memory.get_user = AsyncMock(return_value=user_node)
    cockpit_ctx.agent = MagicMock()
    cockpit_ctx.agent.get_memory = AsyncMock(return_value=memory)
    cockpit_ctx.user_id = "u-1"

    tools = _build_identity_tools(cockpit_ctx)
    get_name = next(t for t in tools if t.name == "get_user_name")
    text = _unwrap(await get_name.call())
    assert "unknown" in text and "ask the user" in text
