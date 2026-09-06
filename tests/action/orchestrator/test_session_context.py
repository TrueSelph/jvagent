"""SESSION CONTEXT (ADR-0042) — turn-stable clock + channel ground truth."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.session_context import render_session_context
from jvagent.action.orchestrator.turn_cache import bind_turn_cache, get_prompt_cache


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
async def test_compose_places_session_context_last():
    """ADR-0049: the per-turn block sits AFTER the stable sections so the
    prompt-cache prefix is everything above it (it used to follow identity,
    which capped the shared prefix at ~200 characters)."""
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
    assert out.index("You are Test") < out.index("AVAILABLE TOOLS")
    assert out.index("OPERATING RULES") < out.index("SESSION CONTEXT")
    assert out.rstrip().endswith("2026-07-27T13:45:00+00:00")


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
        cache = get_prompt_cache()
        captured["session"] = cache.get("session_context", "")
        captured["skills"] = cache.get("skills_section", "")
        return {"action": "final", "answer": "hi"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    await ex.execute(visitor)
    assert "SESSION CONTEXT" in captured.get("session", "")
    assert "2026" in captured.get("session", "")
    assert "CURRENT CHANNEL: web" in captured.get("session", "")
    assert not captured.get("skills", "").startswith("CURRENT CHANNEL")


# --- persisted templates (ADR-0049) ----------------------------------------
#
# ``system_prompt`` is an attribute stored on the action node at bootstrap, so
# moving the SESSION CONTEXT slot in code changed nothing on a running
# deployment — the live prompt still carried the block after identity. A
# persisted copy of any past built-in is "the default" and renders the current
# layout; an operator's own template keeps whatever position it chose.


def _block():
    return (
        "SESSION CONTEXT (authoritative for this turn):\n"
        "CURRENT DATE/TIME: Monday, July 27, 2026 13:45:00 (UTC)\n"
        "ISO 8601: 2026-07-27T13:45:00+00:00\n\n"
    )


def _compose_with(ex):
    return ex._compose_system_prompt(
        identity_section="You are Test, a bot.\n",
        session_context_section=_block(),
        tools_section="(none)",
        skills_section="(none)",
        capabilities_section="(none)",
        parameters_section="(none)",
    )


def test_persisted_pre_0049_template_renders_the_current_layout():
    from jvagent.action.orchestrator import prompts as P

    ex = OrchestratorInteractAction()
    ex.system_prompt = P.ORCHESTRATOR_SYSTEM_PROMPT_PRE_0049
    assert "{session_context_section}{protocol_section}" in ex.system_prompt
    out = _compose_with(ex)
    assert out.index("OPERATING RULES") < out.index("SESSION CONTEXT")
    assert out.count("SESSION CONTEXT") == 1

    # Through the store's fold too (arrows → ``?``, dashes → ``-``).
    from jvspatial.utils.normalization import normalize_text_to_ascii

    ex.system_prompt = normalize_text_to_ascii(P.ORCHESTRATOR_SYSTEM_PROMPT_PRE_0049)
    out = _compose_with(ex)
    assert out.index("OPERATING RULES") < out.index("SESSION CONTEXT")


def test_an_operator_template_keeps_its_own_slot_position():
    ex = OrchestratorInteractAction()
    ex.system_prompt = (
        "{identity_section}{session_context_section}MY RULES.\n"
        "{protocol_section}\nTOOLS:\n{tools_section}\n{parameters_section}\n"
    )
    out = _compose_with(ex)
    assert out.index("SESSION CONTEXT") < out.index("MY RULES")
    assert "TOOLS:" in out
