"""Regression: an already-delivered answer must not be re-emitted.

When ``interaction.response`` already holds the turn's user-facing text, a
``final`` that echoes it must not voice again. A mid-turn publishing tool that
latches ``has_emitted()`` ends the loop (see
``test_emitted_suppresses_followup.py``) so the orchestrator cannot follow up.
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
async def test_publishing_tool_ends_turn_before_final_closer(
    action, make_visitor, monkeypatch
):
    """A non-terminal tool that already delivered user-facing content ends the
    turn; a later ``final`` closer is not invoked."""
    emitted: List[str] = []
    visitor = make_visitor(utterance="any pressure washers?")

    async def _fake_emit_reply(_visitor: Any, text: str) -> None:
        emitted.append(text)

    async def _emit(args: Dict[str, Any]) -> str:
        cur = visitor.interaction.response or ""
        card = f"**{args.get('title', 'item')}** — GYD 1,000\n[View Details](http://x)"
        visitor.interaction.response = f"{cur}\n\n{card}" if cur else card
        return "published card"

    emit = SkillTool(name="emit_catalog_message", description="emit", run=_emit)

    closer = "Would you like to see more options or compare these models?"
    fake_model, calls = _decisions(
        {"action": "tool", "tool": "emit_catalog_message", "args": {"title": "VEVOR"}},
        {"action": "tool", "tool": "emit_catalog_message", "args": {"title": "HONDA"}},
        {"action": "final", "answer": closer},
    )
    monkeypatch.setattr(action, "_run_model", fake_model)
    monkeypatch.setattr(action, "_emit_reply", _fake_emit_reply)

    async def _fake_assemble(
        v, activated, visible, flow_owner, utterance, skill_docs, surface_meta=None
    ):
        visible.add("emit_catalog_message")
        return {"emit_catalog_message": emit}

    monkeypatch.setattr(action, "_assemble_tools", _fake_assemble)

    await action._run_loop(visitor)

    assert calls["n"] == 1
    assert closer not in emitted
    assert closer not in (visitor.interaction.response or "")
    assert "VEVOR" in (visitor.interaction.response or "")


@pytest.mark.asyncio
async def test_final_answer_not_double_emitted_when_already_emitted(
    action, make_visitor, monkeypatch
):
    """Guard still holds: an answer already present in the response is not
    re-emitted (the model echoing an already-emitted line)."""
    emitted: List[str] = []
    visitor = make_visitor(utterance="hi")
    visitor.interaction.response = "Hello there, how can I help?"

    async def _fake_emit_reply(_visitor: Any, text: str) -> None:
        emitted.append(text)

    # The model's final echoes text already in the response → must not re-voice.
    fake_model, _ = _decisions(
        {"action": "final", "answer": "Hello there, how can I help?"},
    )
    monkeypatch.setattr(action, "_run_model", fake_model)
    monkeypatch.setattr(action, "_emit_reply", _fake_emit_reply)

    async def _fake_assemble(
        v, activated, visible, flow_owner, utterance, skill_docs, surface_meta=None
    ):
        return {}

    monkeypatch.setattr(action, "_assemble_tools", _fake_assemble)

    await action._run_loop(visitor)

    assert emitted == [], "an already-emitted answer must not be emitted twice"
