"""Tests for the orchestrator pending-activation guard."""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.orchestrator.loop import _pending_activation_observation


def _visitor(context):
    return SimpleNamespace(conversation=SimpleNamespace(context=context))


def test_pending_activation_returns_guard_observation():
    obs = _pending_activation_observation(
        _visitor(
            {
                "pending_activation": {
                    "skill": "leadgen",
                    "tool": "leadgen__capture",
                    "field": "project_budget",
                }
            }
        )
    )

    assert obs is not None
    assert obs["guard"] == "pending_activation"
    assert "project_budget" in obs["observation"]
    assert "leadgen__capture" in obs["observation"]
    assert "open_fields" in obs["observation"]
    assert "ask_next" in obs["observation"]
    assert "next_optional" not in obs["observation"]


def test_no_pending_activation_returns_none():
    assert _pending_activation_observation(_visitor({})) is None


def test_malformed_pending_activation_returns_none():
    assert (
        _pending_activation_observation(
            _visitor({"pending_activation": {"field": "phone"}})
        )
        is None
    )
