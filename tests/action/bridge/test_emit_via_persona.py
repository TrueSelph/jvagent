"""Tests for the EMIT(via_persona=True) contract.

Phase-2 distillation moved ``deliver_final_response`` out of
ReasoningHelm and into ``BridgeInteractAction._handle_emit``. The
contract is:

1. ReasoningHelm's engine finishes; the helm returns an
   ``EMIT(text=…, finalize=True, via_persona=True, metadata={…})``.
2. Bridge sees ``via_persona=True`` and routes through
   ``deliver_via_persona`` (skill-catalog overrides, degenerate skip,
   per-skill verbatim_final).
3. Reflex's own EMITs (trivial smalltalk) keep ``via_persona=False``
   and publish raw — there's no value in LLM-rewriting "Hey there!".

These tests pin the two ends of the contract: EMIT carries the
persona-routing fields, and Bridge's ``_handle_emit`` branches on them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.bridge_interact_action import BridgeInteractAction
from jvagent.action.bridge.state import BridgeState
from jvagent.action.helm.contracts import EMIT


class TestEmitContractFields:
    """EMIT carries the persona-routing fields."""

    def test_emit_defaults_disable_persona_routing(self):
        # Default EMIT() — what Reflex emits for trivial smalltalk —
        # must NOT trigger persona routing.
        verb = EMIT(text="Hey there!")
        assert verb.via_persona is False
        assert verb.response_mode == "publish"
        assert verb.degenerate_max_chars == 0

    def test_emit_can_carry_full_persona_routing_payload(self):
        verb = EMIT(
            text="The capital of France is Paris.",
            finalize=True,
            via_persona=True,
            response_mode="respond",
            degenerate_max_chars=25,
            metadata={"activated_skills": ["web_search"]},
        )
        assert verb.via_persona is True
        assert verb.response_mode == "respond"
        assert verb.degenerate_max_chars == 25
        assert verb.metadata["activated_skills"] == ["web_search"]


class TestHasPendingDirectives:
    """The ``_has_pending_directives`` helper that gates the
    directive-merge branch of ``_handle_emit``."""

    def test_returns_false_when_no_visitor_interaction(self):
        visitor = MagicMock(interaction=None)
        assert BridgeInteractAction._has_pending_directives(visitor) is False

    def test_returns_false_on_empty_directive_list(self):
        visitor = MagicMock()
        visitor.interaction.directives = []
        assert BridgeInteractAction._has_pending_directives(visitor) is False

    def test_returns_false_when_all_directives_executed(self):
        visitor = MagicMock()
        visitor.interaction.directives = [
            {"text": "Greet the user", "executed": True},
            {"text": "Mention capabilities", "executed": True},
        ]
        assert BridgeInteractAction._has_pending_directives(visitor) is False

    def test_returns_true_when_any_directive_unexecuted(self):
        visitor = MagicMock()
        visitor.interaction.directives = [
            {"text": "Greet the user", "executed": True},
            {"text": "Mention capabilities", "executed": False},
        ]
        assert BridgeInteractAction._has_pending_directives(visitor) is True

    def test_string_directives_are_ignored(self):
        # The directive list is documented as ``List[Dict]`` but
        # defensive code shouldn't crash on string entries (some
        # legacy IAs append raw strings).
        visitor = MagicMock()
        visitor.interaction.directives = ["Greet the user"]
        assert BridgeInteractAction._has_pending_directives(visitor) is False


@pytest.mark.asyncio
class TestPublishEmitViaPersonaBranching:
    """``_publish_emit_via_persona`` picks the right branch.

    Branch A — ``via_persona=True``: route through ``deliver_via_persona``
    (full skill-catalog aware delivery).

    Branch B — pending directives, no ``via_persona``: append helm text
    as a "Tell the user" directive and call ``persona.respond``
    (directive-merge composition).
    """

    async def _make_bridge(self, persona, monkeypatch):
        """Build a BridgeInteractAction with a mocked PersonaAction lookup.

        BridgeInteractAction inherits from a Pydantic model that rejects
        ad-hoc per-instance attribute assignment, so we patch the
        ``get_action`` method on the CLASS for the test's duration.
        Mirrors the pattern used in ``tests/action/bridge/test_helm_resolution.py``.
        """
        bridge = BridgeInteractAction()

        async def _get_action(self, name):
            if name == "PersonaAction":
                return persona
            return None

        monkeypatch.setattr(BridgeInteractAction, "get_action", _get_action)
        return bridge

    async def test_via_persona_branch_uses_deliver_via_persona(self, monkeypatch):
        bridge = await self._make_bridge(
            persona=MagicMock(enabled=True), monkeypatch=monkeypatch
        )

        # Capture deliver_via_persona invocation. Patch where Bridge
        # imports it (deferred import inside the method).
        captured = {}

        async def _fake_deliver_via_persona(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(
            "jvagent.action.helm.reasoning.delivery.persona_delivery.deliver_via_persona",
            _fake_deliver_via_persona,
        )

        visitor = MagicMock()
        visitor.interaction.directives = []  # no pending directives
        visitor._skill_state = {"skill_catalog": "catalog_sentinel"}
        state = BridgeState()
        verb = EMIT(
            text="The answer is 42.",
            finalize=True,
            via_persona=True,
            response_mode="respond",
            degenerate_max_chars=25,
            metadata={"activated_skills": ["web_search"]},
        )

        handled = await bridge._publish_emit_via_persona(visitor, state, verb)
        assert handled is True
        assert captured["content"] == "The answer is 42."
        assert captured["response_mode"] == "respond"
        assert captured["degenerate_response_max_chars"] == 25
        assert captured["skill_catalog"] == "catalog_sentinel"
        # engine_result is constructed only when activated_skills is non-empty
        assert captured["engine_result"] is not None
        assert list(captured["engine_result"].activated_skills) == ["web_search"]

    async def test_directive_merge_branch_uses_persona_respond(self, monkeypatch):
        # No via_persona, but pending directives — go through the legacy
        # directive-merge branch (persona.respond after adding helm text
        # as a "Tell the user" directive).
        persona = MagicMock(enabled=True)
        persona.respond = AsyncMock()
        bridge = await self._make_bridge(persona=persona, monkeypatch=monkeypatch)

        visitor = MagicMock()
        visitor.interaction.directives = [
            {"text": "Greet the user", "executed": False},
        ]
        visitor.add_directive = AsyncMock()
        state = BridgeState()
        verb = EMIT(
            text="Sure — let me explain.",
            finalize=True,
            via_persona=False,  # explicit: directive-merge path only
        )

        handled = await bridge._publish_emit_via_persona(visitor, state, verb)
        assert handled is True
        # Helm draft text was added as a directive.
        visitor.add_directive.assert_awaited_once_with(
            "Tell the user: Sure — let me explain."
        )
        # And persona.respond was called.
        persona.respond.assert_awaited_once()

    async def test_double_render_guard_blocks_second_call(self, monkeypatch):
        # _publish_emit_via_persona must be idempotent within one turn —
        # if the bucket flag is already set, second call returns False.
        persona = MagicMock(enabled=True)
        persona.respond = AsyncMock()
        bridge = await self._make_bridge(persona=persona, monkeypatch=monkeypatch)

        visitor = MagicMock()
        visitor.interaction.directives = [
            {"text": "Greet", "executed": False},
        ]
        visitor.add_directive = AsyncMock()
        state = BridgeState()
        state.helm_states["__bridge__"] = {"directives_rendered": True}

        verb = EMIT(text="Sure.", finalize=True)
        handled = await bridge._publish_emit_via_persona(visitor, state, verb)
        assert handled is False
        # Persona NOT called the second time.
        persona.respond.assert_not_awaited()

    async def test_no_persona_installed_returns_false(self, monkeypatch):
        # When PersonaAction is absent, the helper bails so the caller
        # falls back to raw publish. via_persona request can't be honored
        # without a persona to respond.
        bridge = await self._make_bridge(persona=None, monkeypatch=monkeypatch)

        visitor = MagicMock()
        visitor.interaction.directives = []
        visitor._skill_state = {}
        state = BridgeState()
        verb = EMIT(text="Hello", finalize=True, via_persona=True)

        handled = await bridge._publish_emit_via_persona(visitor, state, verb)
        assert handled is False
