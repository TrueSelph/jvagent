"""Tests for ``PersonaHelm`` (BRIDGE-ROADMAP §G)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.contracts import EMIT, YIELD
from jvagent.action.helm.persona import PersonaHelm

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_visitor() -> MagicMock:
    interaction = MagicMock()
    interaction.id = "int_test"
    interaction.set_to_executed = MagicMock()
    visitor = MagicMock()
    visitor.interaction = interaction
    visitor.add_directive = AsyncMock()
    return visitor


def _make_bridge_state(handoff: dict | None = None) -> MagicMock:
    state = MagicMock()
    state.helm_states = {"PersonaHelm": handoff} if handoff is not None else {}
    return state


def _patch_respond(monkeypatch, *, return_value: str | None = "rendered"):
    """Patch ``PersonaHelm.respond`` to return ``return_value``."""
    called: dict = {"called": False, "kwargs": None}

    async def _respond(self, visitor, directives=None, **kwargs):
        called["called"] = True
        called["kwargs"] = {"directives": directives, **kwargs}
        return return_value

    monkeypatch.setattr(PersonaHelm, "respond", _respond)
    return called


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_step_yields_after_respond_succeeds(monkeypatch):
    helm = PersonaHelm()
    visitor = _make_visitor()
    state = _make_bridge_state()
    record = _patch_respond(monkeypatch, return_value="hi there!")

    result = await helm.step(visitor, state)

    assert isinstance(result, YIELD)
    assert record["called"] is True
    # interaction.set_to_executed called so the walker marks the turn done.
    visitor.interaction.set_to_executed.assert_called_once()


async def test_step_passes_history_limit_from_helm_default(monkeypatch):
    helm = PersonaHelm()
    helm.history_limit = 7
    visitor = _make_visitor()
    state = _make_bridge_state()
    record = _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    assert record["kwargs"]["history_limit"] == 7


async def test_step_uses_handoff_history_limit_when_present(monkeypatch):
    helm = PersonaHelm()
    helm.history_limit = 3
    visitor = _make_visitor()
    state = _make_bridge_state({"history_limit": 12})
    record = _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    assert record["kwargs"]["history_limit"] == 12


async def test_step_uses_handoff_use_history_when_present(monkeypatch):
    helm = PersonaHelm()
    helm.use_history = True
    visitor = _make_visitor()
    state = _make_bridge_state({"use_history": False})
    record = _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    assert record["kwargs"]["use_history"] is False


# ---------------------------------------------------------------------------
# Draft / directive injection
# ---------------------------------------------------------------------------


async def test_step_injects_draft_text_as_persona_directive(monkeypatch):
    helm = PersonaHelm()
    visitor = _make_visitor()
    state = _make_bridge_state({"text": "Answer: Python 3.14.5"})
    _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    # The draft text is wrapped in a "Tell the user: …" directive.
    visitor.add_directive.assert_any_call("Tell the user: Answer: Python 3.14.5")


async def test_step_adds_explicit_directive(monkeypatch):
    helm = PersonaHelm()
    visitor = _make_visitor()
    state = _make_bridge_state({"directive": "Be brief and informal."})
    _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    visitor.add_directive.assert_any_call("Be brief and informal.")


async def test_step_passes_directives_list_to_respond(monkeypatch):
    helm = PersonaHelm()
    visitor = _make_visitor()
    state = _make_bridge_state(
        {"directives": ["one", "  ", "two"]}
    )  # blank entries filtered
    record = _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    assert record["kwargs"]["directives"] == ["one", "two"]


async def test_step_no_directives_when_handoff_empty(monkeypatch):
    helm = PersonaHelm()
    visitor = _make_visitor()
    state = _make_bridge_state()
    record = _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    visitor.add_directive.assert_not_called()
    assert record["kwargs"]["directives"] is None


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------


async def test_step_yields_when_no_interaction():
    helm = PersonaHelm()
    visitor = MagicMock()
    visitor.interaction = None
    state = _make_bridge_state()

    result = await helm.step(visitor, state)
    assert isinstance(result, YIELD)


async def test_step_emits_fallback_when_respond_returns_none(monkeypatch):
    helm = PersonaHelm()
    helm.fallback_text = "fallback text"
    visitor = _make_visitor()
    state = _make_bridge_state()
    _patch_respond(monkeypatch, return_value=None)

    result = await helm.step(visitor, state)

    assert isinstance(result, EMIT)
    assert result.text == "fallback text"
    assert result.finalize is True


async def test_step_emits_fallback_when_respond_raises(monkeypatch):
    helm = PersonaHelm()
    helm.fallback_text = "broken"
    visitor = _make_visitor()
    state = _make_bridge_state()

    async def _boom(self, visitor, **kwargs):
        raise RuntimeError("persona explosion")

    monkeypatch.setattr(PersonaHelm, "respond", _boom)

    result = await helm.step(visitor, state)
    assert isinstance(result, EMIT)
    assert result.text == "broken"


async def test_step_continues_when_add_directive_raises(monkeypatch):
    """``visitor.add_directive`` failure should NOT abort the persona call."""
    helm = PersonaHelm()
    visitor = _make_visitor()
    visitor.add_directive = AsyncMock(side_effect=RuntimeError("can't add"))
    state = _make_bridge_state({"text": "draft"})
    record = _patch_respond(monkeypatch)

    result = await helm.step(visitor, state)

    assert isinstance(result, YIELD)
    assert record["called"] is True


# ---------------------------------------------------------------------------
# Handoff slot shape
# ---------------------------------------------------------------------------


async def test_step_ignores_non_dict_handoff_slot(monkeypatch):
    helm = PersonaHelm()
    visitor = _make_visitor()
    state = MagicMock()
    state.helm_states = {"PersonaHelm": "not a dict"}
    record = _patch_respond(monkeypatch)

    await helm.step(visitor, state)

    # Falls back to default behaviour (no directives).
    visitor.add_directive.assert_not_called()
    assert record["kwargs"]["directives"] is None
