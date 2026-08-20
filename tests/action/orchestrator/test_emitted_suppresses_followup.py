"""A tool that already delivered a user-facing message ends the turn.

When a non-terminal tool publishes (latches ``interaction.emitted``), the
orchestrator must not take another tick to ``reply`` / ``final``. ``reply`` /
``respond`` themselves no-op if the latch is already set — defense in depth
when the model still tries to acknowledge.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import SkillTool


def _decisions(*items: Dict[str, Any]):
    calls = {"n": 0}

    async def _fake_run_model(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        i = calls["n"]
        calls["n"] += 1
        if i < len(items):
            return items[i]
        return {"action": "final", "answer": ""}

    return _fake_run_model, calls


@pytest.fixture
def action():
    return OrchestratorInteractAction()


@pytest.mark.asyncio
async def test_loop_ends_when_non_terminal_tool_already_emitted(
    action, make_visitor, monkeypatch
):
    """QR-style publish: image+caption already went out; do not call reply."""
    emitted: List[str] = []
    visitor = make_visitor(utterance="show me my qrcode")

    async def _fake_emit_reply(_visitor: Any, text: str) -> None:
        emitted.append(text)

    async def _send(_args: Dict[str, Any]) -> str:
        visitor.interaction.response = (
            "Here you go. Scan the QR code above to get your ident code."
        )
        return (
            "QR code image with caption was sent to the user. "
            "Do not send an additional text reply."
        )

    send = SkillTool(name="qr_code__send", description="send qr", run=_send)

    fake_model, calls = _decisions(
        {"action": "tool", "tool": "qr_code__send", "args": {}},
        {
            "action": "tool",
            "tool": "reply",
            "args": {"text": "Your QR code has been sent."},
        },
    )
    monkeypatch.setattr(action, "_run_model", fake_model)
    monkeypatch.setattr(action, "_emit_reply", _fake_emit_reply)

    async def _fake_assemble(
        v, activated, visible, flow_owner, utterance, skill_docs, surface_meta=None
    ):
        visible.add("qr_code__send")
        return {"qr_code__send": send}

    monkeypatch.setattr(action, "_assemble_tools", _fake_assemble)

    await action._run_loop(visitor)

    assert calls["n"] == 1
    assert emitted == []
    assert "Your QR code has been sent." not in (visitor.interaction.response or "")


@pytest.mark.asyncio
async def test_reply_noop_when_already_emitted(
    make_orchestrator, make_visitor, monkeypatch
):
    """If the turn already delivered, reply/respond must not call _send_reply."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()
    sent: List[str] = []

    async def _cap(self, visitor, text="", *, compose=False):
        sent.append(text)

    monkeypatch.setattr(OrchestratorInteractAction, "_send_reply", _cap)

    ex = make_orchestrator(
        actions=[reply],
        decisions=[
            {
                "action": "tool",
                "tool": "reply",
                "args": {"text": "Your QR code has been sent."},
            },
        ],
    )
    v = make_visitor(utterance="show me my qrcode")
    v.interaction.response = (
        "Here you go. Scan the QR code above to get your ident code."
    )

    await ex.execute(v)

    assert sent == []
    assert v.interaction.response == (
        "Here you go. Scan the QR code above to get your ident code."
    )
