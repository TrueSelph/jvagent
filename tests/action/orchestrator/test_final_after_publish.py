"""Regression: a distinct ``final`` answer must survive a mid-turn publish.

Non-terminal publishing tools (e.g. a catalog ``emit_catalog_message``) append
their content to ``interaction.response`` while the loop is still running. When
the loop then ends with a ``final`` action carrying a *different* string — a
product skill's closing line — that closer must still be voiced. The earlier
``_maybe_voice_final`` guard treated any non-empty ``interaction.response`` as
"already voiced" and silently dropped the closer (observed live: catalog cards
emitted, closer generated as ``{"action":"final","answer":"Would you like…"}``,
never delivered).
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
async def test_final_closer_voiced_after_publishing_tool(
    action, make_visitor, monkeypatch
):
    """Card emit populates response mid-turn; the distinct closer still voices."""
    voiced: List[str] = []
    visitor = make_visitor(utterance="any pressure washers?")

    async def _fake_voice(_visitor: Any, text: str) -> None:
        voiced.append(text)
        # mirror real _voice → _pipe_response: persists to interaction.response
        cur = visitor.interaction.response or ""
        visitor.interaction.response = f"{cur}\n\n{text}" if cur else text

    async def _emit(args: Dict[str, Any]) -> str:
        # mirror emit_catalog_message → response_bus → append-to-response
        cur = visitor.interaction.response or ""
        card = f"**{args.get('title', 'item')}** — GYD 1,000\n[View Details](http://x)"
        visitor.interaction.response = f"{cur}\n\n{card}" if cur else card
        return "published card"

    emit = SkillTool(name="emit_catalog_message", description="emit", run=_emit)

    # Two card emits (non-terminal), then a final closer distinct from the cards.
    closer = "Would you like to see more options or compare these models?"
    fake_model, _ = _decisions(
        {"action": "tool", "tool": "emit_catalog_message", "args": {"title": "VEVOR"}},
        {"action": "tool", "tool": "emit_catalog_message", "args": {"title": "HONDA"}},
        {"action": "final", "answer": closer},
    )
    monkeypatch.setattr(action, "_run_model", fake_model)
    monkeypatch.setattr(action, "_voice", _fake_voice)

    async def _fake_assemble(
        v, activated, visible, flow_owner, utterance, skill_docs, surface_meta=None
    ):
        visible.add("emit_catalog_message")
        return {"emit_catalog_message": emit}

    monkeypatch.setattr(action, "_assemble_tools", _fake_assemble)

    await action._run_loop(visitor)

    assert closer in voiced, (
        "the closing line returned via action=final was dropped because the "
        "catalog emits had populated interaction.response"
    )
    assert closer in (visitor.interaction.response or "")


@pytest.mark.asyncio
async def test_final_answer_not_double_voiced_when_already_emitted(
    action, make_visitor, monkeypatch
):
    """Guard still holds: an answer already present in the response is not
    re-voiced (the model echoing an already-voiced line)."""
    voiced: List[str] = []
    visitor = make_visitor(utterance="hi")
    visitor.interaction.response = "Hello there, how can I help?"

    async def _fake_voice(_visitor: Any, text: str) -> None:
        voiced.append(text)

    # The model's final echoes text already in the response → must not re-voice.
    fake_model, _ = _decisions(
        {"action": "final", "answer": "Hello there, how can I help?"},
    )
    monkeypatch.setattr(action, "_run_model", fake_model)
    monkeypatch.setattr(action, "_voice", _fake_voice)

    async def _fake_assemble(
        v, activated, visible, flow_owner, utterance, skill_docs, surface_meta=None
    ):
        return {}

    monkeypatch.setattr(action, "_assemble_tools", _fake_assemble)

    await action._run_loop(visitor)

    assert voiced == [], "an already-voiced answer must not be emitted twice"
