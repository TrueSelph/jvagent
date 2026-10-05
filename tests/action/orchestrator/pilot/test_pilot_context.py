"""Pilot turns preserve only authenticated host context and trusted JV rules."""

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import jvagent.action.orchestrator.session_context as session_context
from jvagent.action.interact import session_token
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


def _signed_context(*, secret: str, run_id: str, context: str) -> dict[str, str]:
    body = json.dumps(
        {"version": 1, "run_id": run_id, "context": context},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {"body": body, "signature": signature}


@pytest.mark.parametrize(
    ("channel", "context", "context_text", "trusted"),
    [
        (
            "workspace-chat",
            _signed_context(
                secret="pilot-test-secret",
                run_id="run-1",
                context="Follow the authenticated host policy.",
            ),
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
            _signed_context(
                secret="pilot-test-secret",
                run_id="run-1",
                context="Verified host policy.",
            ),
            "Verified host policy.",
            True,
        ),
    ],
)
async def test_pilot_instructions_include_only_verified_host_context(
    monkeypatch, channel, context, context_text, trusted
):
    monkeypatch.setattr(session_token, "_secret", lambda: "pilot-test-secret")
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
    assert "activate the relevant skill" in instructions
    assert "declared Actions" in instructions
    assert "use the research Actions" not in instructions
    if channel == "workspace-chat":
        assert "Configured channel policy." in instructions
    if trusted:
        assert context_text in instructions
        assert "HOST CONTEXT (authenticated for this turn)" in instructions
    else:
        assert context_text not in instructions
        assert "HOST CONTEXT (authenticated for this turn)" not in instructions
