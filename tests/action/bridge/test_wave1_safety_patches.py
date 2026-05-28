"""Regression tests for Wave-1 Bridge safety patches (May 2026 review).

Covers the items from the external code review that survive ADR-0009:

- **C2** — ``DELEGATE.args`` propagation: when a helm returns
  ``DELEGATE(interact_action="X", args={...})`` Bridge must stash the
  args on the visitor so the target IA can read them, and clear the
  slot after the IA finishes.
- **H2** — ``helm.step()`` exception containment: if a helm raises,
  Bridge must catch, log, and route to ``_safe_fallback`` rather than
  letting the exception propagate up the walker.
- **H5** — Error-path bus publish: ``ReasoningHelm._handle_error`` must
  publish the fallback text on the response bus, not only write it to
  ``interaction.response``.
- **H6** — ``routing_source`` in helm_shift event payload: every
  appended event must include the field so operators can filter logs
  by cascade layer.

The **C1** pattern-orchestrator exclusion test was removed in Wave 9
when the engine router subsystem was deleted (ADR-0009). The exclusion
itself is preserved via the ``manifest.pattern_orchestrator`` flag,
exercised by ``tests/action/test_manifest.py`` and the Reflex
peer-awareness tests.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.bridge_interact_action import (
    DELEGATE_ARGS_VISITOR_ATTR,
    BridgeInteractAction,
    get_delegate_args,
)
from jvagent.action.helm.contracts import DELEGATE, EMIT, YIELD

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# C2 — DELEGATE.args propagation
# ---------------------------------------------------------------------------


class TestDelegateArgsPropagationC2:
    """Helm-supplied DELEGATE.args reach the target IA via the visitor."""

    async def test_args_visible_to_target(self, make_bridge, make_visitor, stub_helm):
        """Target IA sees ``verb.args`` through ``get_delegate_args(visitor)``.

        Constructs a fake target IA that records what
        ``get_delegate_args`` returns during its execute() and asserts
        it matches the args the helm passed.
        """
        # Track what the target sees.
        captured: Dict[str, Any] = {}

        from jvagent.action.interact.base import InteractAction

        class CapturingTargetIA(InteractAction):
            async def execute(self, visitor: Any) -> None:  # type: ignore[override]
                captured["args"] = get_delegate_args(visitor)

        target = CapturingTargetIA()

        # Helm scripted to DELEGATE with args.
        helm = stub_helm(
            name="ReflexHelm",
            script=[
                DELEGATE(
                    interact_action="CapturingTargetIA",
                    args={"intent": "lookup_product", "sku": "ABC-123"},
                )
            ],
        )

        bridge = make_bridge(helms={"ReflexHelm": helm})
        bridge._test_action_registry["CapturingTargetIA"] = target

        visitor = make_visitor()
        await bridge.execute(visitor)

        assert captured.get("args") == {
            "intent": "lookup_product",
            "sku": "ABC-123",
        }, (
            f"Target IA should see DELEGATE.args via get_delegate_args(); "
            f"got {captured.get('args')!r}"
        )

    async def test_args_cleared_after_target_execute(
        self, make_bridge, make_visitor, stub_helm
    ):
        """The visitor slot must be cleared so a sibling DELEGATE can't see stale args.

        If args persisted across DELEGATEs in the same turn, a second
        IA dispatched later would observe the previous IA's args. This
        is what the ``finally``-clause clear in ``_handle_delegate`` is
        guarding against.
        """
        from jvagent.action.interact.base import InteractAction

        class ProbingIA(InteractAction):
            async def execute(self, visitor: Any) -> None:  # type: ignore[override]
                # No-op — we'll inspect the slot AFTER execute().
                pass

        probe = ProbingIA()
        helm = stub_helm(
            name="ReflexHelm",
            script=[DELEGATE(interact_action="ProbingIA", args={"x": 1})],
        )

        bridge = make_bridge(helms={"ReflexHelm": helm})
        bridge._test_action_registry["ProbingIA"] = probe

        visitor = make_visitor()
        await bridge.execute(visitor)

        # After Bridge finishes dispatching, the slot must be cleared.
        assert getattr(visitor, DELEGATE_ARGS_VISITOR_ATTR, None) is None, (
            "DELEGATE_ARGS_VISITOR_ATTR must be cleared after _handle_delegate "
            "so subsequent DELEGATEs do not observe stale args."
        )

    async def test_delegate_without_args_is_safe(
        self, make_bridge, make_visitor, stub_helm
    ):
        """DELEGATE with no args → target sees None from ``get_delegate_args``.

        Helms that don't need to pass args (most do not) must continue
        to work; the helper returns None rather than raising.
        """
        captured: Dict[str, Any] = {"args": "sentinel"}

        from jvagent.action.interact.base import InteractAction

        class NoArgsIA(InteractAction):
            async def execute(self, visitor: Any) -> None:  # type: ignore[override]
                captured["args"] = get_delegate_args(visitor)

        target = NoArgsIA()
        helm = stub_helm(
            name="ReflexHelm",
            script=[DELEGATE(interact_action="NoArgsIA")],  # no args
        )
        bridge = make_bridge(helms={"ReflexHelm": helm})
        bridge._test_action_registry["NoArgsIA"] = target

        visitor = make_visitor()
        await bridge.execute(visitor)

        assert captured["args"] is None, (
            "get_delegate_args() must return None when the DELEGATE verb "
            "carried no args (no false positive from a stale slot)."
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_delegate_args_returns_none_on_missing_slot(self):
        """The helper is safe on a visitor with no slot at all.

        IAs invoked outside Bridge (direct walker queue, no DELEGATE)
        call ``get_delegate_args(visitor)`` and must get None, not an
        AttributeError or similar.

        (Kept ``async`` for consistency with the module's pytestmark;
        the helper itself is pure-sync.)
        """

        class EmptyVisitor:
            pass

        assert get_delegate_args(EmptyVisitor()) is None

    async def test_get_delegate_args_rejects_non_dict(self):
        """The helper defends against an attacker stashing a string in the slot.

        If something accidentally writes a non-dict value to the
        visitor slot, ``get_delegate_args`` must return None rather
        than expose the wrong shape to consumers.
        """

        class WeirdVisitor:
            pass

        v = WeirdVisitor()
        setattr(v, DELEGATE_ARGS_VISITOR_ATTR, "not-a-dict")
        assert get_delegate_args(v) is None


# ---------------------------------------------------------------------------
# H2 — helm.step() exception containment
# ---------------------------------------------------------------------------


class TestHelmStepExceptionH2:
    """If a helm raises during step(), Bridge safe-falls back rather than crash."""

    async def test_helm_step_exception_routes_to_safe_fallback(
        self, make_bridge, make_visitor, stub_helm, publish_log
    ):
        """A raising helm step → user sees ``denied_response_text``, no exception escapes."""
        from jvagent.action.helm.stub_helm import StubHelm

        class RaisingHelm(StubHelm):
            async def step(self, visitor, state):  # type: ignore[override]
                raise RuntimeError("simulated helm crash")

            def helm_name(self) -> str:  # type: ignore[override]
                return "RaisingHelm"

        raising = RaisingHelm()
        bridge = make_bridge(
            helms={"RaisingHelm": raising},
            denied_text="Sorry — something went wrong.",
        )

        visitor = make_visitor()

        # The previous behaviour was the RuntimeError propagating up through
        # _dispatch and crashing the walker. Now Bridge catches and falls back.
        await bridge.execute(visitor)  # must not raise

        # The user sees the safe-fallback text, not silence.
        publishes = [p for p in publish_log if p.get("content")]
        assert any(
            "Sorry — something went wrong." in p["content"] for p in publishes
        ), (
            f"Expected the denied_response_text in publish_log after a "
            f"helm.step() exception; got {publishes}"
        )


# ---------------------------------------------------------------------------
# H5 — Bus publish on ReasoningHelm error path
# ---------------------------------------------------------------------------


class TestReasoningErrorBusPublishH5:
    """``_handle_error`` must publish on the bus, not only write interaction.response.

    The previous implementation only set ``interaction.response`` and
    saved it. Channels that consume the response-bus stream (Slack /
    WhatsApp / streaming web client) saw silence on engine errors.
    """

    async def test_handle_error_publishes_via_bus(self):
        """publish() is called with the fallback text on the error path."""
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()

        # Stub publish — capture what gets sent to the bus.
        published: List[Dict[str, Any]] = []

        async def _fake_publish(self, *, visitor, content, **kwargs):
            published.append({"content": content, **kwargs})
            return None

        # Class-level monkeypatch via __dict__ to bypass Pydantic restrictions.
        ReasoningHelm.__dict__  # access; we'll patch via setattr
        original_publish = ReasoningHelm.publish
        ReasoningHelm.publish = _fake_publish  # type: ignore[assignment]
        try:
            # Build a visitor with an empty interaction so the error path
            # actually publishes (it skips when response is already set).
            interaction = MagicMock()
            interaction.id = "int_err"
            interaction.response = ""
            interaction.save = AsyncMock()

            visitor = MagicMock()
            visitor.interaction = interaction
            visitor.session_id = "s_err"
            visitor.stream = False

            # ``clear_session`` reads the agent — give it a benign one.
            async def _get_agent_stub(self):
                return None

            from jvagent.action.helm.reasoning import reasoning_helm as rh_mod

            # Patch clear_session to a no-op so we don't depend on its surface.
            original_clear = rh_mod.clear_session
            rh_mod.clear_session = lambda v: None  # type: ignore[assignment]
            try:
                await helm._handle_error(visitor, RuntimeError("boom"))
            finally:
                rh_mod.clear_session = original_clear  # type: ignore[assignment]
        finally:
            ReasoningHelm.publish = original_publish  # type: ignore[assignment]

        # The fallback text must have been published.
        assert len(published) == 1, (
            f"Expected exactly one bus publish on the error path; got "
            f"{len(published)}: {published}"
        )
        assert "error processing your request" in published[0]["content"], (
            f"Bus publish should contain the fallback text; got "
            f"{published[0]['content']!r}"
        )
        # And interaction.response is also set for non-bus channels.
        assert "error processing your request" in interaction.response

    async def test_handle_error_skips_publish_if_response_already_streamed(
        self,
    ):
        """If a real response was already streamed, don't append an error addendum.

        Mid-loop streaming may have already given the user a partial
        answer before the engine errored. Tacking on "...I encountered
        an error..." would be more confusing than helpful.
        """
        from jvagent.action.helm.reasoning import reasoning_helm as rh_mod
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        helm = ReasoningHelm()
        published: List[Dict[str, Any]] = []

        async def _fake_publish(self, *, visitor, content, **kwargs):
            published.append({"content": content, **kwargs})
            return None

        original_publish = ReasoningHelm.publish
        ReasoningHelm.publish = _fake_publish  # type: ignore[assignment]
        original_clear = rh_mod.clear_session
        rh_mod.clear_session = lambda v: None  # type: ignore[assignment]
        try:
            interaction = MagicMock()
            interaction.id = "int_partial"
            interaction.response = "Here is what I found so far …"
            interaction.save = AsyncMock()

            visitor = MagicMock()
            visitor.interaction = interaction

            await helm._handle_error(visitor, RuntimeError("late failure"))
        finally:
            ReasoningHelm.publish = original_publish  # type: ignore[assignment]
            rh_mod.clear_session = original_clear  # type: ignore[assignment]

        assert published == [], (
            "publish() must be skipped when interaction.response already "
            "contains streamed content."
        )
        # The previously-streamed text is preserved untouched.
        assert interaction.response == "Here is what I found so far …"


# ---------------------------------------------------------------------------
# H6 — routing_source in helm_shift event payload
# ---------------------------------------------------------------------------


class TestRoutingSourceInEventH6:
    """Every ``helm_shift`` event in observability_metrics carries routing_source."""

    async def test_initial_shift_event_includes_routing_source(
        self, make_bridge, make_visitor, stub_helm
    ):
        """The first-visit ``initial`` shift records routing_source=initial."""
        helm = stub_helm(name="ReflexHelm", script=[YIELD()])
        bridge = make_bridge(helms={"ReflexHelm": helm})
        visitor = make_visitor()

        await bridge.execute(visitor)

        events = [
            e
            for e in visitor.interaction.observability_metrics
            if e.get("event_type") == "helm_shift"
        ]
        assert events, "Expected at least one helm_shift event"
        # The initial pick.
        initial = events[0]
        assert "routing_source" in initial["data"], (
            f"helm_shift event missing routing_source key; got keys "
            f"{list(initial['data'].keys())}"
        )
        assert initial["data"]["routing_source"] == "initial", (
            f"First helm_shift event should have routing_source=initial; "
            f"got {initial['data']['routing_source']!r}"
        )

    async def test_helm_delegate_event_includes_routing_source(
        self, make_bridge, make_visitor, stub_helm
    ):
        """A helm-initiated DELEGATE records routing_source=helm_delegate."""
        from jvagent.action.interact.base import InteractAction

        class TargetIA(InteractAction):
            async def execute(self, visitor: Any) -> None:  # type: ignore[override]
                return None

        helm = stub_helm(
            name="ReflexHelm",
            script=[DELEGATE(interact_action="TargetIA")],
        )
        bridge = make_bridge(helms={"ReflexHelm": helm})
        bridge._test_action_registry["TargetIA"] = TargetIA()

        visitor = make_visitor()
        await bridge.execute(visitor)

        events = [
            e
            for e in visitor.interaction.observability_metrics
            if e.get("event_type") == "helm_shift"
        ]
        delegate_events = [
            e for e in events if e["data"].get("routing_source") == "helm_delegate"
        ]
        assert len(delegate_events) == 1, (
            f"Expected exactly one helm_delegate event; got events with "
            f"routing_sources={[e['data'].get('routing_source') for e in events]}"
        )
        # Sanity — the source/target matches the verb.
        assert delegate_events[0]["data"]["to_helm"] == "TargetIA"
