"""Regression tests for Wave-2 Bridge safety patches (May 2026 review).

Covers:

- **H1** — AccessControl on initial helm pick. The previous code path
  picked the default helm and ran it without ever consulting AC. A
  user denied the default helm would still run it on the first visit
  (SHIFT/DELEGATE were AC-gated, but "initial" was not). Bridge now
  walks ``default_helm`` first, then ``helms[]`` in order, picking the
  first allowed helm. If all helms are denied, routes to
  ``_safe_fallback`` and records the AC denial in the shift log.

- **H3** — ReasoningHelm per-turn orchestration state migration. The
  ``_step_outcome`` and ``_pending_final_emit`` fields previously lived
  as instance attributes on the ReasoningHelm singleton (one Action
  shared by every concurrent interaction on the agent). Two
  simultaneous turns would cross-pollute — one turn's
  ``self._step_outcome = "yield"`` was observable to the other turn's
  ``_step_impl``. The state now lives in
  ``bridge_state.helm_states[helm_name]`` which is rebuilt fresh per
  interaction. These tests confirm two parallel ``_step_impl`` calls
  don't trample each other's state.

- **M5** — ``handoff_state`` merge semantics. A SHIFT with
  ``handoff_state`` used to replace the target helm's slot wholesale,
  nuking any state the target helm had previously written (e.g.
  ReasoningHelm's ``pending_ias`` chain from an earlier visit). It now
  merges: SHIFTing helm's keys override only the keys it explicitly
  supplies; the rest of the slot survives.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.access import BridgeAccessDenied
from jvagent.action.bridge.bridge_interact_action import BridgeInteractAction
from jvagent.action.bridge.state import (
    BRIDGE_STATE_VISITOR_ATTR,
    BridgeState,
)
from jvagent.action.helm.contracts import SHIFT, YIELD
from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# H1 — AC on initial helm pick
# ---------------------------------------------------------------------------


class TestInitialHelmAccessControlH1:
    """Bridge must AC-check the initial helm and walk down on denial."""

    async def test_no_ac_picks_default(self, make_bridge, make_visitor, stub_helm):
        """No AccessControlAction installed → behaviour is unchanged.

        Sanity baseline — the walk-down must collapse to the original
        ``_pick_initial_helm`` order when AC isn't enforcing. ``make_bridge``
        defaults to an agent without an AC, which is the ``ac is None`` path
        inside ``check_helm_access``.
        """
        reflex = stub_helm(name="ReflexHelm", script=[YIELD()])
        reasoning = stub_helm(name="ReasoningHelm", script=[YIELD()])
        bridge = make_bridge(
            helms={"ReflexHelm": reflex, "ReasoningHelm": reasoning},
            helm_names=["ReflexHelm", "ReasoningHelm"],
            default_helm="ReflexHelm",
        )

        visitor = make_visitor()
        await bridge.execute(visitor)

        # ``_persist_observability`` writes the shift log under
        # ``interaction.parameters["bridge_observability"]["shift_log"]``
        # — see tests/action/bridge/test_observability.py for the pattern.
        obs = visitor.interaction.parameters.get("bridge_observability") or {}
        shift_log = obs.get("shift_log") or []
        assert shift_log, "Bridge should record at least the initial shift"
        first = shift_log[0]
        assert first["to_helm"] == "ReflexHelm"
        assert first["routing_source"] == "initial"

    async def test_ac_denies_default_walks_to_next(
        self, monkeypatch, make_bridge, make_visitor, stub_helm
    ):
        """Default helm denied → Bridge picks the next allowed helm."""
        from jvagent.action.bridge import bridge_interact_action as bridge_mod

        call_log: List[str] = []

        async def fake_check_helm_access(agent, *, helm_name, user_id, channel) -> None:
            call_log.append(helm_name)
            if helm_name == "ReflexHelm":
                raise BridgeAccessDenied(
                    f"helm:{helm_name}", user_id=user_id, channel=channel
                )
            return None  # allow

        monkeypatch.setattr(bridge_mod, "check_helm_access", fake_check_helm_access)

        reflex = stub_helm(name="ReflexHelm", script=[YIELD()])
        reasoning = stub_helm(name="ReasoningHelm", script=[YIELD()])
        bridge = make_bridge(
            helms={"ReflexHelm": reflex, "ReasoningHelm": reasoning},
            helm_names=["ReflexHelm", "ReasoningHelm"],
            default_helm="ReflexHelm",
        )

        visitor = make_visitor()
        await bridge.execute(visitor)

        # AC was consulted for both — default first (denied), then next.
        assert call_log == [
            "ReflexHelm",
            "ReasoningHelm",
        ], f"AC walk-down order incorrect; got {call_log}"
        obs = visitor.interaction.parameters.get("bridge_observability") or {}
        shift_log = obs.get("shift_log") or []
        assert shift_log, "Bridge should record the initial shift"
        assert shift_log[0]["to_helm"] == "ReasoningHelm"
        assert shift_log[0]["routing_source"] == "initial"

    async def test_all_denied_safe_fallback(
        self, monkeypatch, make_bridge, make_visitor, stub_helm, publish_log
    ):
        """Every helm denied → safe_fallback with denied_response_text."""
        from jvagent.action.bridge import bridge_interact_action as bridge_mod

        async def deny_all(agent, *, helm_name, user_id, channel) -> None:
            raise BridgeAccessDenied(
                f"helm:{helm_name}", user_id=user_id, channel=channel
            )

        monkeypatch.setattr(bridge_mod, "check_helm_access", deny_all)

        reflex = stub_helm(name="ReflexHelm", script=[YIELD()])
        reasoning = stub_helm(name="ReasoningHelm", script=[YIELD()])
        bridge = make_bridge(
            helms={"ReflexHelm": reflex, "ReasoningHelm": reasoning},
            helm_names=["ReflexHelm", "ReasoningHelm"],
            default_helm="ReflexHelm",
            denied_text="No access for you.",
        )

        visitor = make_visitor()
        await bridge.execute(visitor)

        # User sees the denial fallback message.
        denied_publishes = [
            p for p in publish_log if "No access for you." in (p.get("content") or "")
        ]
        assert denied_publishes, (
            f"Expected denied_response_text in publish_log; got "
            f"{[p.get('content') for p in publish_log]}"
        )
        # The shift log records the AC denial under safe_fallback routing_source.
        obs = visitor.interaction.parameters.get("bridge_observability") or {}
        shift_log = obs.get("shift_log") or []
        ac_denied = [
            rec for rec in shift_log if rec["routing_source"] == "safe_fallback"
        ]
        assert ac_denied, (
            f"Expected a safe_fallback shift-log entry recording the AC "
            f"denial; got routing_sources="
            f"{[r['routing_source'] for r in shift_log]}"
        )

    async def test_candidate_order_default_first(self):
        """``_initial_helm_candidate_order`` orders default first, then helms[]."""
        bridge = BridgeInteractAction()
        bridge.helms = ["ReflexHelm", "ReasoningHelm", "SpecialistHelm"]
        bridge.default_helm = "ReasoningHelm"

        resolved = {
            "ReflexHelm": MagicMock(),
            "ReasoningHelm": MagicMock(),
            "SpecialistHelm": MagicMock(),
        }
        order = bridge._initial_helm_candidate_order(resolved)
        # default first, then helms[] in declared order (deduped).
        assert order == [
            "ReasoningHelm",
            "ReflexHelm",
            "SpecialistHelm",
        ], f"Candidate order wrong; got {order}"

    async def test_candidate_order_no_default(self):
        """No default_helm → walk in declared helms[] order."""
        bridge = BridgeInteractAction()
        bridge.helms = ["ReflexHelm", "ReasoningHelm"]
        bridge.default_helm = ""

        resolved = {
            "ReflexHelm": MagicMock(),
            "ReasoningHelm": MagicMock(),
        }
        order = bridge._initial_helm_candidate_order(resolved)
        assert order == ["ReflexHelm", "ReasoningHelm"]


# ---------------------------------------------------------------------------
# H3 — ReasoningHelm per-turn state isolation
# ---------------------------------------------------------------------------


class TestReasoningHelmPerTurnStateH3:
    """ReasoningHelm orchestration state must be per-turn, not per-instance.

    The previous singleton-attribute design (``self._step_outcome``,
    ``self._pending_final_emit``) would cross-pollute under concurrent
    use — both fields are now read/written via the bridge_state slot.
    """

    def _make_visitor_with_state(self) -> Any:
        """Visitor with a fresh BridgeState attached."""
        interaction = MagicMock()
        interaction.id = "int_test"
        interaction.set_to_executed = MagicMock()
        interaction.response = ""
        interaction.save = AsyncMock()
        visitor = MagicMock()
        visitor.interaction = interaction
        visitor.conversation = MagicMock()
        visitor._skill_state = {}
        visitor.prepend = AsyncMock()
        setattr(
            visitor,
            BRIDGE_STATE_VISITOR_ATTR,
            BridgeState(turn_started_at=0.0),
        )
        return visitor

    async def test_no_class_level_step_outcome_attr(self):
        """The class no longer has the old singleton attributes.

        If someone re-introduces ``_step_outcome`` as an instance/class
        attribute the cross-pollination bug is back. This test pins the
        migration.
        """
        # Pydantic ``ModelPrivateAttr`` would still show via vars() if
        # someone re-added it as a non-ClassVar private attr.
        assert "_step_outcome" not in ReasoningHelm.__dict__, (
            "ReasoningHelm._step_outcome was reintroduced as a class "
            "attribute — Wave-2 H3 moved this state to "
            "bridge_state.helm_states. Use _get/_set_step_outcome instead."
        )
        assert "_pending_final_emit" not in ReasoningHelm.__dict__, (
            "ReasoningHelm._pending_final_emit was reintroduced. Use "
            "_get/_set_pending_final_emit instead."
        )

    async def test_set_get_step_outcome_round_trip(self):
        """Setter writes; getter reads back from the per-turn slot."""
        helm = ReasoningHelm()
        visitor = self._make_visitor_with_state()

        # Initially absent.
        assert helm._get_step_outcome(visitor) is None

        helm._set_step_outcome(visitor, "yield")
        assert helm._get_step_outcome(visitor) == "yield"

        helm._set_step_outcome(visitor, "continue")
        assert helm._get_step_outcome(visitor) == "continue"

        helm._set_step_outcome(visitor, None)
        assert helm._get_step_outcome(visitor) is None

    async def test_set_get_pending_final_emit_round_trip(self):
        """Setter writes; getter reads back from the per-turn slot."""
        helm = ReasoningHelm()
        visitor = self._make_visitor_with_state()

        assert helm._get_pending_final_emit(visitor) is None

        payload = {"text": "hi", "activated_skills": []}
        helm._set_pending_final_emit(visitor, payload)
        got = helm._get_pending_final_emit(visitor)
        assert got == payload

        helm._set_pending_final_emit(visitor, None)
        assert helm._get_pending_final_emit(visitor) is None

    async def test_concurrent_visitors_do_not_cross_pollute(self):
        """The exact bug Wave-2 H3 fixed: two visitors don't share state.

        Construct two visitors with independent BridgeStates. Set
        different step_outcomes on each. The singleton helm instance
        must report different values for each visitor.

        With the OLD singleton-attribute design, both visitors would
        read whichever value was set last — the bug the migration
        eliminates.
        """
        helm = ReasoningHelm()  # singleton — shared across both turns
        visitor_a = self._make_visitor_with_state()
        visitor_b = self._make_visitor_with_state()

        helm._set_step_outcome(visitor_a, "yield")
        helm._set_step_outcome(visitor_b, "continue")

        assert helm._get_step_outcome(visitor_a) == "yield"
        assert helm._get_step_outcome(visitor_b) == "continue"

        # Same independence for pending_final_emit.
        helm._set_pending_final_emit(visitor_a, {"text": "A", "activated_skills": []})
        helm._set_pending_final_emit(visitor_b, {"text": "B", "activated_skills": []})

        emit_a = helm._get_pending_final_emit(visitor_a)
        emit_b = helm._get_pending_final_emit(visitor_b)
        assert emit_a is not None and emit_a["text"] == "A"
        assert emit_b is not None and emit_b["text"] == "B"

    async def test_helpers_safe_on_bypassed_bridge(self):
        """Without BridgeState on the visitor, helpers no-op gracefully.

        Test/diagnostic paths that call ``_orchestrate`` without going
        through Bridge must not crash on the helpers — getters return
        None, setters silently no-op.
        """
        helm = ReasoningHelm()
        bare_visitor = MagicMock()

        # Remove bridge_state attribute (MagicMock auto-creates everything).
        # Use a real plain object instead so getattr() honestly returns None.
        class Bare:
            pass

        bare = Bare()
        assert helm._get_step_outcome(bare) is None
        assert helm._get_pending_final_emit(bare) is None
        # Setters must not raise.
        helm._set_step_outcome(bare, "yield")
        helm._set_pending_final_emit(bare, {"text": "x", "activated_skills": []})


# ---------------------------------------------------------------------------
# M5 — handoff_state merge semantics
# ---------------------------------------------------------------------------


class TestHandoffStateMergeM5:
    """SHIFT.handoff_state must MERGE into the target slot, not REPLACE it."""

    async def test_handoff_state_preserves_existing_keys(
        self, make_bridge, make_visitor, stub_helm
    ):
        """Target helm's prior slot keys survive a SHIFT with partial handoff_state.

        Scenario: ReasoningHelm wrote ``{"pending_ias": [...]}`` to its
        slot on visit N. On visit N+1, ReflexHelm SHIFTs back to
        ReasoningHelm with ``handoff_state={"foo": "bar"}``. After the
        merge, the slot must contain BOTH keys.
        """
        # Pre-populate ReasoningHelm's slot with state that must survive.
        reflex = stub_helm(
            name="ReflexHelm",
            script=[
                SHIFT(
                    target="ReasoningHelm",
                    reason="test merge",
                    handoff_state={"new_key": "new_value"},
                )
            ],
        )
        reasoning = stub_helm(name="ReasoningHelm", script=[YIELD()])

        bridge = make_bridge(
            helms={"ReflexHelm": reflex, "ReasoningHelm": reasoning},
            helm_names=["ReflexHelm", "ReasoningHelm"],
            default_helm="ReflexHelm",
        )

        visitor = make_visitor()
        # Pre-stamp BridgeState so we can pre-populate the target slot.
        state = BridgeState(turn_started_at=0.0)
        state.helm_states["ReasoningHelm"] = {
            "pending_ias": ["IntroIA", "HandoffIA"],
            "step_outcome": "yield",
        }
        setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, state)

        await bridge.execute(visitor)

        # After the SHIFT, the merged slot has the PRIOR keys
        # (pending_ias, step_outcome) AND the new key (new_key).
        slot = state.helm_states["ReasoningHelm"]
        assert slot.get("pending_ias") == ["IntroIA", "HandoffIA"], (
            f"pending_ias was clobbered by handoff_state merge — "
            f"slot is now {slot!r}"
        )
        assert (
            slot.get("step_outcome") == "yield"
        ), f"step_outcome was clobbered — slot is {slot!r}"
        assert (
            slot.get("new_key") == "new_value"
        ), f"new key from handoff_state missing — slot is {slot!r}"

    async def test_handoff_state_overrides_overlapping_keys(
        self, make_bridge, make_visitor, stub_helm
    ):
        """When handoff_state and prior slot share a key, handoff_state wins.

        Merge semantics: SHIFTing helm explicitly supplied keys take
        precedence. Anything it didn't mention survives untouched.
        """
        reflex = stub_helm(
            name="ReflexHelm",
            script=[
                SHIFT(
                    target="ReasoningHelm",
                    reason="override test",
                    handoff_state={"step_outcome": "continue"},
                )
            ],
        )
        reasoning = stub_helm(name="ReasoningHelm", script=[YIELD()])

        bridge = make_bridge(
            helms={"ReflexHelm": reflex, "ReasoningHelm": reasoning},
            helm_names=["ReflexHelm", "ReasoningHelm"],
            default_helm="ReflexHelm",
        )

        visitor = make_visitor()
        state = BridgeState(turn_started_at=0.0)
        state.helm_states["ReasoningHelm"] = {
            "pending_ias": ["X"],
            "step_outcome": "yield",
        }
        setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, state)

        await bridge.execute(visitor)

        slot = state.helm_states["ReasoningHelm"]
        # Overlapping key — handoff_state wins.
        assert slot.get("step_outcome") == "continue"
        # Non-overlapping key — survives.
        assert slot.get("pending_ias") == ["X"]
