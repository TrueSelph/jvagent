"""SESSION CONTEXT (ADR-0042) — turn-stable clock + channel ground truth."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.session_context import render_session_context


class _FakeApp:
    def __init__(self, now: datetime):
        self._now = now

    async def now(self, fmt=None):
        return self._now


@pytest.mark.asyncio
async def test_render_session_context_includes_frozen_clock_and_channel():
    now = datetime(2026, 7, 27, 13, 45, 0, tzinfo=timezone.utc)
    visitor = SimpleNamespace(channel="web")
    text = await render_session_context(visitor, app=_FakeApp(now))
    assert "SESSION CONTEXT" in text
    assert "2026" in text
    assert "ISO 8601: 2026-07-27T13:45:00+00:00" in text
    assert "CURRENT CHANNEL: web" in text
    assert "training cutoff" in text.lower() or "guessed year" in text


@pytest.mark.asyncio
async def test_render_session_context_omits_channel_when_empty():
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    visitor = SimpleNamespace(channel="")
    text = await render_session_context(visitor, app=_FakeApp(now))
    assert "CURRENT CHANNEL" not in text
    assert "2026" in text


@pytest.mark.asyncio
async def test_compose_places_session_context_after_identity():
    ex = OrchestratorInteractAction()
    out = ex._compose_system_prompt(
        identity_section="You are Test, a bot.\n",
        session_context_section=(
            "SESSION CONTEXT (authoritative for this turn):\n"
            "CURRENT DATE/TIME: Monday, July 27, 2026 13:45:00 (UTC)\n"
            "ISO 8601: 2026-07-27T13:45:00+00:00\n\n"
        ),
        tools_section="(none)",
        skills_section="(none)",
        capabilities_section="(none)",
        parameters_section="(none)",
    )
    assert "SESSION CONTEXT" in out
    assert out.index("You are Test") < out.index("SESSION CONTEXT")
    assert out.index("SESSION CONTEXT") < out.index("AVAILABLE TOOLS")


@pytest.mark.asyncio
async def test_prepare_turn_caches_session_context_not_on_skills(
    make_orchestrator, make_visitor, monkeypatch
):
    now = datetime(2026, 7, 27, 9, 0, 0, tzinfo=timezone.utc)

    async def _get_app(self):
        return _FakeApp(now)

    monkeypatch.setattr(OrchestratorInteractAction, "get_app", _get_app)
    ex = make_orchestrator(decisions=[{"action": "final", "answer": "hi"}])
    visitor = make_visitor(utterance="what year is it?", channel="web")
    # Drive prepare via execute; prompt cache should hold SESSION CONTEXT.
    captured = {}

    async def _rm(self, *a, **k):
        cache = getattr(self, "_turn_prompt_cache", {}) or {}
        captured["session"] = cache.get("session_context", "")
        captured["skills"] = cache.get("skills_section", "")
        return {"action": "final", "answer": "hi"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    await ex.execute(visitor)
    assert "SESSION CONTEXT" in captured.get("session", "")
    assert "2026" in captured.get("session", "")
    assert "CURRENT CHANNEL: web" in captured.get("session", "")
    assert not captured.get("skills", "").startswith("CURRENT CHANNEL")
