"""Pilot turns preserve only authenticated host context and trusted JV rules."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import jvagent.action.orchestrator.session_context as session_context
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


@pytest.mark.parametrize(
    ("channel", "context", "context_text", "trusted"),
    [
        (
            "workspace-chat",
            {"context": "untrusted client data"},
            "Follow the authenticated host policy.",
            True,
        ),
        (
            "workspace-chat",
            {"body": '{"context":"forged"}', "signature": "invalid"},
            "forged",
            False,
        ),
        (
            "default",
            {"context": "untrusted client data"},
            "Verified host policy.",
            True,
        ),
    ],
)
async def test_pilot_instructions_include_only_verified_host_context(
    monkeypatch, channel, context, context_text, trusted
):
    monkeypatch.setattr(
        session_context,
        "render_session_context",
        AsyncMock(return_value="SESSION CONTEXT: trusted clock and channel."),
    )
    orchestrator = OrchestratorInteractAction()
    orchestrator.channel_overrides = {
        "workspace-chat": {"system_prompt_extra": "Configured channel policy."}
    }
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_app", AsyncMock(return_value=None)
    )
    visitor = SimpleNamespace(
        channel=channel,
        data={"run_id": "run-1", "host_system_context": context},
        _host_system_context=context_text if trusted else None,
    )
    responder = SimpleNamespace(
        _identity=AsyncMock(return_value="Agent identity."),
        _compose_parameters_text=lambda _persona, _interaction: "Response policy.",
    )

    instructions = await orchestrator._pilot_run_instructions(
        visitor, channel, responder, object()
    )

    assert "Agent identity." in instructions
    assert "Response policy." in instructions
    assert "SESSION CONTEXT: trusted clock and channel." in instructions
    assert "scoped to evidence-backed research" in instructions
    assert "loaded research skill and only its declared Actions" in instructions
    assert "each paired with source_ids observed in Action results" in instructions
    assert "only from validated" in instructions
    assert "do not put additional findings in other fields" in instructions
    assert "use the research Actions" not in instructions
    if channel == "workspace-chat":
        assert "Configured channel policy." in instructions
    if trusted:
        assert context_text in instructions
        assert "HOST CONTEXT (authenticated for this turn)" in instructions
    else:
        assert context_text not in instructions
        assert "HOST CONTEXT (authenticated for this turn)" not in instructions
