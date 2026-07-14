"""build_core_tools tier gating (minimal | standard | full) and the
get_current_datetime tool's runtime output; build_artifact_tools surface."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from jvagent.action.orchestrator.core_tools import (
    build_artifact_tools,
    build_core_tools,
)
from jvagent.memory.conversation import Conversation


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


async def test_artifact_tools_absent_without_conversation():
    # No conversation on the visitor -> no artifact tools surfaced.
    assert build_artifact_tools(MagicMock(), SimpleNamespace(conversation=None)) == []


async def test_artifact_tools_list_and_get(test_db):
    conv = await Conversation.create(
        session_id=f"art-{uuid.uuid4().hex[:12]}", user_id="u", channel="default"
    )
    try:
        i1 = await conv.add_interaction(utterance="hi")
        await conv.add_artifact(
            i1,
            name="vis1",
            data="a red car on a hill",
            summary="red car",
            source="vision",
            tags=["image"],
        )
        tools = {
            t.name: t
            for t in build_artifact_tools(
                MagicMock(), SimpleNamespace(conversation=conv)
            )
        }
        assert set(tools) == {"list_artifacts", "get_artifact"}

        listing = await tools["list_artifacts"].run({})
        assert "vis1" in listing and "red car" in listing
        # summary-only listing must not leak the full payload
        assert "on a hill" not in listing

        full = await tools["get_artifact"].run({"name": "vis1"})
        assert "a red car on a hill" in full
        assert "(no such artifact" in await tools["get_artifact"].run({"name": "nope"})
        assert "requires a 'name'" in await tools["get_artifact"].run({})
    finally:
        await conv.delete(cascade=True)
