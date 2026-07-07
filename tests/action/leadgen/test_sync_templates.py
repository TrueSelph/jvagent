"""Tests for leadgen sync template substitution and thresholds."""

import pytest

from jvagent.action.leadgen import sync as sync_mod


class _FakeMCP:
    def __init__(self, names):
        self._names = list(names)

    def get_server_names(self):
        return list(self._names)


class _FakeAction:
    """Minimal stand-in exposing get_action('MCPAction')."""

    def __init__(self, mcp):
        self._mcp = mcp

    async def get_action(self, name):
        return self._mcp


def _dest(server="google_sheets"):
    return {
        "server": server,
        "mode": "mcp",
        "tool": "sheets_append_values",
        "arguments": {"values": ["{profile_row}"]},
    }


@pytest.mark.asyncio
async def test_sync_skips_unconfigured_connector():
    action = _FakeAction(_FakeMCP([]))  # MCP enabled but server not registered
    results, any_success = await sync_mod.sync_to_destinations(
        action, [_dest()], {"name": "Jane"}, "u1"
    )
    assert any_success is False
    assert results["google_sheets"] == "skipped: connector not configured"


@pytest.mark.asyncio
async def test_sync_skips_when_mcp_disabled():
    action = _FakeAction(None)  # MCPAction not enabled on the agent
    results, any_success = await sync_mod.sync_to_destinations(
        action, [_dest()], {"name": "Jane"}, "u1"
    )
    assert any_success is False
    assert "skipped" in results["google_sheets"]


def test_substitute_profile_row():
    data = {"name": "Jane", "email": "jane@example.com"}
    result = sync_mod.substitute("{profile_row}", data, "user-1")
    assert "Jane" in result
    assert "jane@example.com" in result


def test_substitute_field_token():
    data = {"name": "Jane"}
    assert sync_mod.substitute("{name}", data, "u1") == "Jane"


def test_sync_threshold_met_require_any():
    data = {"name": "Jane", "email": "jane@example.com"}
    assert sync_mod.sync_threshold_met(data, ["name"], ["phone", "email"])


def test_sync_threshold_not_met_missing_min():
    data = {"email": "jane@example.com"}
    assert not sync_mod.sync_threshold_met(data, ["name"], ["phone", "email"])


def test_digest_legacy_key():
    data = {"_lead_sync_mcp_DIGEST": "abc"}
    assert sync_mod.get_stored_digest(data) == "abc"
