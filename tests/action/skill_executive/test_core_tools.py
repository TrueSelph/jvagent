"""build_core_tools tier gating (minimal | standard | full) and the
get_current_datetime tool's runtime output."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jvagent.action.skill_executive.core_tools import build_core_tools

pytestmark = pytest.mark.asyncio


def _names(tier):
    return {t.name for t in build_core_tools(MagicMock(), tier)}


async def test_minimal_tier_excludes_standard_tools():
    # get_current_datetime is a standard-tier tool → absent at minimal.
    assert "get_current_datetime" not in _names("minimal")


async def test_standard_and_full_include_datetime():
    assert "get_current_datetime" in _names("standard")
    assert "get_current_datetime" in _names("full")


async def test_unknown_tier_falls_back_to_standard():
    assert _names("bogus") == _names("standard")
    assert _names("") == _names("standard")


async def test_datetime_tool_runs():
    tools = {t.name: t for t in build_core_tools(MagicMock(), "standard")}
    out = await tools["get_current_datetime"].run({})
    # Returns a non-empty string mentioning the year (ISO-ish datetime).
    assert isinstance(out, str) and out.strip()
