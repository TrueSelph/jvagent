"""Tests for leadgen sync template substitution and thresholds."""

import pytest

from jvagent.action.leadgen import sync as sync_mod


class _FakeClient:
    """Records call_tool invocations; returns a non-error MCP result."""

    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, args):
        self.calls.append((tool_name, args))
        return {"isError": False, "content": []}


class _FakeMCP:
    def __init__(self, names):
        self._names = list(names)
        self.client = _FakeClient()

    def get_server_names(self):
        return list(self._names)

    async def get_client_for_user(self, server, user_id):
        return self.client

    def get_client(self, server):
        return self.client


class _FakeAction:
    """Minimal stand-in exposing get_action('MCPAction')."""

    def __init__(self, mcp):
        self._mcp = mcp

    async def get_action(self, name):
        return self._mcp


def _dest(server="leadstore"):
    return {
        "server": server,
        "mode": "mcp",
        "tool": "append_record",
        "arguments": {"values": ["{profile_row}"]},
    }


@pytest.mark.asyncio
async def test_sync_skips_unconfigured_connector():
    action = _FakeAction(_FakeMCP([]))  # MCP enabled but server not registered
    results, any_success = await sync_mod.sync_to_destinations(
        action, [_dest()], {"name": "Jane"}, "u1"
    )
    assert any_success is False
    assert results["leadstore"] == "skipped: connector not configured"


@pytest.mark.asyncio
async def test_sync_skips_when_mcp_disabled():
    action = _FakeAction(None)  # MCPAction not enabled on the agent
    results, any_success = await sync_mod.sync_to_destinations(
        action, [_dest()], {"name": "Jane"}, "u1"
    )
    assert any_success is False
    assert "skipped" in results["leadstore"]


def test_substitute_profile_row():
    data = {"name": "Jane", "email": "jane@example.com"}
    result = sync_mod.substitute("{profile_row}", data, "user-1")
    assert "Jane" in result
    assert "jane@example.com" in result


def test_substitute_field_token():
    data = {"name": "Jane"}
    assert sync_mod.substitute("{name}", data, "u1") == "Jane"


def test_substitute_excludes_internal_keys():
    """Internal (_) keys must not leak into synced output."""
    data = {"name": "Jane", sync_mod.DIGEST_KEY: "deadbeef"}
    out = sync_mod.substitute("x {profile_json}", data, "u1")
    assert "Jane" in out
    assert sync_mod.DIGEST_KEY not in out
    assert "deadbeef" not in out
    row = sync_mod.substitute("{profile_row}", data, "u1")
    assert "deadbeef" not in row


def test_sync_threshold_met_require_any():
    data = {"name": "Jane", "email": "jane@example.com"}
    assert sync_mod.sync_threshold_met(data, ["name"], ["phone", "email"])


def test_sync_threshold_not_met_missing_min():
    data = {"email": "jane@example.com"}
    assert not sync_mod.sync_threshold_met(data, ["name"], ["phone", "email"])


def test_digest_legacy_key():
    data = {"_lead_sync_mcp_DIGEST": "abc"}
    assert sync_mod.get_stored_digest(data) == "abc"


def test_digest_ignores_internal_keys():
    """The stored digest key must not change the digest — else dedup never fires."""
    base = {"name": "Jane", "email": "jane@x.com"}
    d0 = sync_mod.compute_digest(base)
    stamped = dict(base)
    stamped[sync_mod.DIGEST_KEY] = d0
    assert sync_mod.compute_digest(stamped) == d0


@pytest.mark.asyncio
async def test_sync_dispatches_to_mcp_with_substituted_args():
    import json

    mcp = _FakeMCP(["leadstore"])
    action = _FakeAction(mcp)
    profile = {"name": "Jane", "email": "jane@x.com"}

    results, ok = await sync_mod.sync_to_destinations(action, [_dest()], profile, "u1")

    assert ok is True
    assert results["leadstore"] == "ok"
    assert len(mcp.client.calls) == 1
    tool_name, args = mcp.client.calls[0]
    assert tool_name == "append_record"
    # {profile_row} template was substituted with the real field values
    assert "Jane" in json.dumps(args)
    assert "jane@x.com" in json.dumps(args)


@pytest.mark.asyncio
async def test_sync_dedups_unchanged_profile():
    mcp = _FakeMCP(["leadstore"])
    action = _FakeAction(mcp)
    profile = {"name": "Jane", "email": "jane@x.com"}
    # simulate the digest stored after a prior successful sync
    profile[sync_mod.DIGEST_KEY] = sync_mod.compute_digest(profile)

    results, ok = await sync_mod.sync_to_destinations(action, [_dest()], profile, "u1")

    assert ok is False
    assert results.get("_digest") == "unchanged"
    assert mcp.client.calls == []  # no duplicate dispatch


@pytest.mark.asyncio
async def test_sync_resyncs_when_profile_changes():
    mcp = _FakeMCP(["leadstore"])
    action = _FakeAction(mcp)
    profile = {"name": "Jane", "email": "jane@x.com"}
    profile[sync_mod.DIGEST_KEY] = sync_mod.compute_digest(profile)
    # a real field changed since the last sync
    profile["phone"] = "+12025550100"

    results, ok = await sync_mod.sync_to_destinations(action, [_dest()], profile, "u1")

    assert ok is True
    assert len(mcp.client.calls) == 1
