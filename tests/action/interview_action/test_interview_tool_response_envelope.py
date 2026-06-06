"""Tests for the standardized interview_tool_response envelope."""

from __future__ import annotations

import json

from jvagent.action.interview_action.responses import (
    POST_TOOL_RESULT_KEYS,
    interview_tool_response,
    slim_post_tool_entry,
)


def test_interview_tool_response_includes_system_message():
    raw = interview_tool_response(
        ok=True,
        status="not_registered",
        exists=False,
        system_message="No existing customer found with this phone number.",
    )
    parsed = json.loads(raw)
    assert parsed["ok"] is True
    assert parsed["status"] == "not_registered"
    assert parsed["system_message"] == (
        "No existing customer found with this phone number."
    )
    assert "message" not in parsed
    assert "phone" not in parsed
    assert "customer" not in parsed


def test_interview_tool_response_omits_none_fields():
    raw = interview_tool_response(ok=True, status="ok")
    parsed = json.loads(raw)
    assert set(parsed.keys()) == {"ok", "status"}


def test_slim_post_tool_entry_whitelist():
    full = {
        "ok": True,
        "status": "customer_exists",
        "system_message": "Registered.",
        "exists": True,
        "interview_complete": True,
        "response_directive": "Tell the user.",
        "phone": "5926431531",
        "customer": {"id": "cust-1"},
        "tracking_status": {"status": "pending"},
    }
    slim = slim_post_tool_entry("verify_phone_number", full)
    assert slim["tool"] == "verify_phone_number"
    assert slim["ok"] is True
    assert slim["status"] == "customer_exists"
    assert slim["system_message"] == "Registered."
    assert slim["exists"] is True
    assert slim["interview_complete"] is True
    assert slim["response_directive"] == "Tell the user."
    assert "phone" not in slim
    assert "customer" not in slim
    assert "tracking_status" not in slim
    assert set(POST_TOOL_RESULT_KEYS) >= {
        "ok",
        "status",
        "system_message",
        "exists",
        "interview_complete",
        "response_directive",
    }
