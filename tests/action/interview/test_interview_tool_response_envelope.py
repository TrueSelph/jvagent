"""Tests for the standardized interview_tool_response envelope."""

from __future__ import annotations

import json

from jvagent.action.interview.hooks import (
    HOOK_RESULT_KEYS,
    interview_tool_response,
    no_session_directive,
    slim_hook_entry,
)


def test_interview_tool_response_includes_system_message():
    raw = interview_tool_response(
        ok=True,
        status="active",
        system_message="No existing customer found with this phone number.",
    )
    parsed = json.loads(raw)
    assert parsed["ok"] is True
    assert parsed["status"] == "active"
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


def test_interview_tool_response_derives_ok_from_status():
    assert json.loads(interview_tool_response(status="active"))["ok"] is True
    assert json.loads(interview_tool_response(status="error"))["ok"] is False
    assert (
        json.loads(interview_tool_response(status="validation_failed"))["ok"] is False
    )


def test_slim_hook_entry_whitelist():
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
    slim = slim_hook_entry("verify_phone_number", full)
    assert slim["tool"] == "verify_phone_number"
    assert slim["ok"] is True
    assert slim["status"] == "customer_exists"
    assert slim["system_message"] == "Registered."
    assert slim["interview_complete"] is True
    assert slim["response_directive"] == "Tell the user."
    assert "exists" not in slim
    assert "phone" not in slim
    assert "customer" not in slim
    assert "tracking_status" not in slim
    assert set(HOOK_RESULT_KEYS) >= {
        "ok",
        "status",
        "system_message",
        "interview_complete",
        "response_directive",
        "next_tool",
    }


def test_no_session_directive_forbids_reply_roleplay():
    directive = no_session_directive()
    assert "use_skill" in directive
    assert "interview__next_field" in directive
    assert "reply" in directive.lower()
