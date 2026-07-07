"""Tests for leadgen sync template substitution and thresholds."""

from jvagent.action.leadgen import sync as sync_mod


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
