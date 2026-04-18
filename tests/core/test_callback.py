"""Tests for task callback webhook behavior."""

from jvagent.core.callback import _safe_webhook_target


def test_safe_webhook_target_redacts_path_and_query():
    target = _safe_webhook_target("https://example.com/path?token=secret")
    assert target == "https://example.com"


def test_safe_webhook_target_handles_invalid_input():
    assert _safe_webhook_target("not-a-url") == "[invalid-webhook-url]"
