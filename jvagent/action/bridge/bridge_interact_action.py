"""``BridgeInteractAction`` — multi-helm orchestrator at weight ``-200``.

Bridge sits in the same agent slot as ``CockpitInteractAction`` (the two are
not installed together; PATTERNS.md enforces this). Its job is to:

1. Resolve the **current helm** for the turn (defaulting to ``default_helm`` or
   the first entry of ``helms`` when no turn state exists yet).
2. Call ``helm.step(visitor, bridge_state)`` exactly once per walker visit
   (one model call per visit — ADR-0002 invariant).
3. Dispatch the returned ``HelmStepResult`` verb:

   - ``EMIT(finalize=True)``  → publish; clear state; end turn.
   - ``EMIT(finalize=False)`` → publish; re-enqueue current helm.
   - ``EXECUTE``              → record state; re-enqueue current helm.
     (Real tool dispatch arrives in later milestones; B is the verb scaffold.)
   - ``SHIFT``                → AC-check ``tool:helm:{target}``; record
     ``ShiftRecord``; emit ack-on-shift when target is deliberate/long;
     decrement shift budget; re-enqueue.
   - ``DELEGATE``             → AC-check ``tool:delegate:{action}``; resolve
     and run the rails ``InteractAction`` inline; re-enqueue.
   - ``YIELD``                → clear state and let the walker continue.

4. Enforce a **shift budget** (default 4 SHIFTs per turn) and a **first-emit
   timeout safety-net** (default 800ms). Both feed the safe-fallback path.
5. Refuse to execute when no helms are configured.

State plumbing lives on ``visitor._bridge_state`` (see :class:`BridgeState`),
parallel to cockpit's ``visitor._skill_state``. The two never coexist on the
same walker visit.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.bridge.access import (
    BridgeAccessDenied,
    check_delegate_access,
    check_helm_access,
)
from jvagent.action.bridge.state import (
    BRIDGE_STATE_VISITOR_ATTR,
    DEFAULT_FIRST_EMIT_TIMEOUT_MS,
    DEFAULT_SHIFT_BUDGET,
    BridgeState,
)
from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import (
    CONTINUE,
    DELEGATE,
    EMIT,
    EXECUTE,
    SHIFT,
    YIELD,
    HelmStepResult,
)
from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Latency classes that warrant an ack-on-shift when transient_ack is provided.
_ACK_ELIGIBLE_LATENCY_CLASSES = frozenset({"deliberate", "long"})


class BridgeConfigurationError(RuntimeError):
    """Raised when Bridge is asked to execute without any usable helms."""


class BridgeInteractAction(InteractAction):
    """Multi-helm orchestrator InteractAction.

    Configuration (override in ``agent.yaml.context:``):

    - ``helms``: ordered list of helm class names (e.g. ``["ReflexHelm",
      "ReasoningHelm"]``). Bridge resolves each via ``Action.get_action(name)``
      at execute time. At least one must resolve to a ``BaseHelm`` instance,
      otherwise Bridge raises :class:`BridgeConfigurationError`.
    - ``default_helm``: class name of the helm to start each turn with.
      Defaults to the first entry of ``helms``.
    - ``shift_budget_per_turn``: hard cap on ``SHIFT`` verbs per turn.
    - ``first_emit_timeout_ms``: if no helm emits within this deadline, Bridge
      publishes ``safety_net_ack_text`` once per turn before continuing.
    - ``safety_net_ack_text``: text published when the first-emit timeout
      fires.
    - ``denied_response_text``: text published when the safe-fallback path
      activates (shift budget exhausted OR AC denies every reachable helm).
    """

    weight: int = attribute(
        default=-200, description="Execution weight (same slot as cockpit)."
    )
    description: str = attribute(
        default=(
            "Multi-helm orchestrator: composes Reflex / Reasoning / Specialist / "
            "Persona helms with explicit shift verbs, shift budget, first-emit "
            "timeout safety net, and AccessControl gating per shift target."
        )
    )

    helms: List[str] = attribute(
        default_factory=list,
        description="Ordered list of helm class names to load.",
    )
    default_helm: str = attribute(
        default="",
        description="Helm class name to start each turn with. Defaults to helms[0].",
    )
    shift_budget_per_turn: int = attribute(
        default=DEFAULT_SHIFT_BUDGET,
        description="Hard cap on SHIFT verbs per turn (default 4).",
    )
    first_emit_timeout_ms: int = attribute(
        default=DEFAULT_FIRST_EMIT_TIMEOUT_MS,
        description="If no EMIT by this deadline, fire safety-net ack once (default 800).",
    )
    safety_net_ack_text: str = attribute(
        default="Working on it…",
        description="Text published when the first-emit timeout fires.",
    )
    denied_response_text: str = attribute(
        default="Sorry, I can't do that here.",
        description="Text published when the safe-fallback path activates.",
    )

    # ------------------------------------------------------------------
    # Helm resolution (overridable in tests)
    # ------------------------------------------------------------------

    async def _lookup_helm(self, name: str) -> Optional[BaseHelm]:
        """Return the helm instance for ``name`` or ``None`` if not loaded.

        Tests can monkeypatch this method to inject ``StubHelm`` instances
        without going through the loader. Production path delegates to
        ``Action.get_action()`` which is O(1) via the class-name cache.
        """
        helm: Any
        try:
            helm = await self.get_action(name)
        except Exception as exc:
            logger.warning("bridge: get_action(%r) raised: %s", name, exc)
            return None
        if helm is None:
            return None
        if not isinstance(helm, BaseHelm):
            logger.warning(
                "bridge: resolved action %r is not a BaseHelm (got %s); ignoring",
                name,
                type(helm).__name__,
            )
            return None
        return helm

    async def _resolve_helms_map(self) -> Dict[str, BaseHelm]:
        """Resolve ``self.helms`` into a ``{name: BaseHelm}`` mapping.

        Helms that fail to resolve are dropped with a warning. Bridge refuses
        to continue (raises ``BridgeConfigurationError``) when the resulting
        map is empty.
        """
        resolved: Dict[str, BaseHelm] = {}
        for name in self.helms or []:
            helm = await self._lookup_helm(name)
            if helm is not None:
                resolved[helm.helm_name()] = helm
        if not resolved:
            raise BridgeConfigurationError(
                "BridgeInteractAction requires at least one resolvable helm "
                f"in helms={self.helms!r}; none were found."
            )
        return resolved

    def _pick_initial_helm(self, resolved: Dict[str, BaseHelm]) -> BaseHelm:
        """Choose the helm Bridge should start a fresh turn with."""
        if self.default_helm and self.default_helm in resolved:
            return resolved[self.default_helm]
        # Fall back to the first declared helm that resolved.
        for name in self.helms or []:
            if name in resolved:
                return resolved[name]
        # Last resort: any resolved helm.
        return next(iter(resolved.values()))

    # ------------------------------------------------------------------
    # State plumbing
    # ------------------------------------------------------------------

    def _get_or_init_state(self, visitor: "InteractWalker") -> BridgeState:
        state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR, None)
        if state is None or not isinstance(state, BridgeState):
            state = BridgeState(
                turn_started_at=time.monotonic(),
                shift_budget_remaining=self.shift_budget_per_turn,
            )
            setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, state)
        return state

    def _clear_state(self, visitor: "InteractWalker") -> None:
        if hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR):
            try:
                delattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
            except AttributeError:
                pass

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def execute(self, visitor: "InteractWalker") -> None:
        """Run one Bridge step: resolve helm, call ``step()``, dispatch verb."""
        # Stamp self on the visitor so helms can reference the Bridge IA
        # (e.g. to pass to walker-queue curation that expects the IA
        # actually present in the queue — helms themselves are not).
        try:
            visitor._bridge_action = self  # type: ignore[attr-defined]
        except Exception:
            pass

        resolved = await self._resolve_helms_map()
        state = self._get_or_init_state(visitor)

        # Resolve current helm on first visit (or whenever it has been cleared).
        if state.current_helm is None:
            initial = self._pick_initial_helm(resolved)
            state.current_helm = initial.helm_name()
            state.record_shift(
                from_helm=None,
                to_helm=state.current_helm,
                reason="bridge:initial",
                ack_emitted=False,
                at_monotonic=time.monotonic(),
            )
        elif state.current_helm not in resolved:
            # Defensive: current_helm vanished between visits (helm unloaded
            # mid-turn). Fall back to safe-default response.
            logger.warning(
                "bridge: current_helm=%r missing from resolved helms; using fallback",
                state.current_helm,
            )
            await self._safe_fallback(visitor, state)
            return

        # First-emit-timeout safety net — fires at most once per turn.
        await self._maybe_emit_safety_net(visitor, state)

        helm = resolved[state.current_helm]
        result = await helm.step(visitor, state)

        if not self._is_valid_verb(result):
            logger.error(
                "bridge: helm %r returned non-verb result %r; safe-falling back",
                state.current_helm,
                type(result).__name__,
            )
            await self._safe_fallback(visitor, state)
            return

        await self._dispatch(visitor, state, resolved, helm, result)

    # ------------------------------------------------------------------
    # Verb dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_verb(result: Any) -> bool:
        return isinstance(result, (EMIT, EXECUTE, CONTINUE, SHIFT, DELEGATE, YIELD))

    async def _dispatch(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        resolved: Dict[str, BaseHelm],
        current_helm: BaseHelm,
        verb: HelmStepResult,
    ) -> None:
        if isinstance(verb, EMIT):
            await self._handle_emit(visitor, state, verb)
            return
        if isinstance(verb, EXECUTE):
            await self._handle_execute(visitor, state, current_helm, verb)
            return
        if isinstance(verb, CONTINUE):
            await self._handle_continue(visitor, state, verb)
            return
        if isinstance(verb, SHIFT):
            await self._handle_shift(visitor, state, resolved, current_helm, verb)
            return
        if isinstance(verb, DELEGATE):
            await self._handle_delegate(visitor, state, verb)
            return
        if isinstance(verb, YIELD):
            self._handle_yield(visitor, state)
            return
        # Defensive — _is_valid_verb already gates this.
        raise RuntimeError(f"unhandled HelmStepResult variant: {type(verb).__name__}")

    # -- EMIT ----------------------------------------------------------

    async def _handle_emit(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        verb: EMIT,
    ) -> None:
        await self.publish(
            visitor=visitor,
            content=verb.text,
            channel=verb.channel,
            metadata=verb.metadata or None,
        )
        state.last_emit_at = time.monotonic()
        if verb.finalize:
            state.finalized = True
            self._clear_state(visitor)
            return
        # Partial emit — same helm continues on next visit.
        await visitor.prepend([self])

    # -- EXECUTE -------------------------------------------------------

    async def _handle_execute(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        current_helm: BaseHelm,
        verb: EXECUTE,
    ) -> None:
        # Real tool dispatch is wired in later milestones. At B, Bridge
        # records the request into helm-scoped state so the helm can observe
        # that the verb was accepted and re-enqueues itself for continuation.
        slot = state.helm_states.setdefault(current_helm.helm_name(), {})
        if isinstance(slot, dict):
            history = slot.setdefault("_pending_tool_calls", [])
            history.extend(
                {
                    "name": tc.name,
                    "arguments": dict(tc.arguments),
                    "call_id": tc.call_id,
                }
                for tc in verb.tool_calls
            )
        await visitor.prepend([self])

    # -- CONTINUE ------------------------------------------------------

    async def _handle_continue(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        verb: CONTINUE,
    ) -> None:
        """Re-enqueue the current helm with no state mutation.

        Bridge owns walker queue, budget, and gear trace; CONTINUE is the
        helm's way of saying "I've done my own work this visit (likely an
        internal model call + tool dispatch) — please visit me again."
        ``verb.reason`` is informational only and surfaces in logs.
        """
        if verb.reason:
            logger.debug("bridge: CONTINUE (%s)", verb.reason)
        await visitor.prepend([self])

    # -- SHIFT ---------------------------------------------------------

    async def _handle_shift(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        resolved: Dict[str, BaseHelm],
        current_helm: BaseHelm,
        verb: SHIFT,
    ) -> None:
        if state.shift_budget_remaining <= 0:
            logger.warning(
                "bridge: shift budget exhausted (count=%d); safe-falling back",
                state.shift_count,
            )
            await self._safe_fallback(visitor, state)
            return
        if verb.target not in resolved:
            logger.warning(
                "bridge: SHIFT target %r is not a resolved helm; safe-falling back",
                verb.target,
            )
            await self._safe_fallback(visitor, state)
            return

        # AccessControl gate.
        try:
            agent = await self.get_agent()
        except Exception as exc:
            logger.warning("bridge: get_agent failed during SHIFT: %s", exc)
            agent = None
        try:
            await check_helm_access(
                agent,
                helm_name=verb.target,
                user_id=getattr(visitor, "user_id", None),
                channel=getattr(visitor, "channel", "default") or "default",
            )
        except BridgeAccessDenied as denied:
            logger.info("bridge: SHIFT denied by AC: %s", denied.resource)
            await self._safe_fallback(visitor, state)
            return

        ack_emitted = False
        target_helm = resolved[verb.target]
        if verb.transient_ack and self._is_ack_eligible(target_helm):
            await self.publish(
                visitor=visitor,
                content=verb.transient_ack,
                transient=True,
            )
            ack_emitted = True
            state.last_emit_at = time.monotonic()

        # Persist handoff state on the target helm's slot.
        if verb.handoff_state is not None:
            state.helm_states[verb.target] = dict(verb.handoff_state)

        state.record_shift(
            from_helm=current_helm.helm_name(),
            to_helm=verb.target,
            reason=verb.reason,
            ack_emitted=ack_emitted,
            at_monotonic=time.monotonic(),
            handoff_state=verb.handoff_state,
        )
        state.shift_budget_remaining -= 1
        state.current_helm = verb.target
        await visitor.prepend([self])

    def _is_ack_eligible(self, target_helm: BaseHelm) -> bool:
        """Decide whether ``transient_ack`` should be emitted before a SHIFT.

        At B the decision reads ``target_helm.latency_class`` directly.
        Milestone E rewires this to consult the loaded manifest.
        """
        return (
            target_helm.latency_class or ""
        ).lower() in _ACK_ELIGIBLE_LATENCY_CLASSES

    # -- DELEGATE ------------------------------------------------------

    async def _handle_delegate(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        verb: DELEGATE,
    ) -> None:
        try:
            agent = await self.get_agent()
        except Exception as exc:
            logger.warning("bridge: get_agent failed during DELEGATE: %s", exc)
            agent = None
        try:
            await check_delegate_access(
                agent,
                action_name=verb.interact_action,
                user_id=getattr(visitor, "user_id", None),
                channel=getattr(visitor, "channel", "default") or "default",
            )
        except BridgeAccessDenied as denied:
            logger.info("bridge: DELEGATE denied by AC: %s", denied.resource)
            await self._safe_fallback(visitor, state)
            return

        target: Any
        try:
            target = await self.get_action(verb.interact_action)
        except Exception as exc:
            logger.warning(
                "bridge: get_action(%r) failed during DELEGATE: %s",
                verb.interact_action,
                exc,
            )
            target = None
        if target is None:
            logger.warning(
                "bridge: DELEGATE target %r not resolvable; safe-falling back",
                verb.interact_action,
            )
            await self._safe_fallback(visitor, state)
            return

        state.delegated_action = verb.interact_action
        try:
            await target.execute(visitor)
        except Exception:
            logger.exception(
                "bridge: DELEGATE target %r raised during execute",
                verb.interact_action,
            )
            state.delegated_action = None
            await self._safe_fallback(visitor, state)
            return
        state.delegated_action = None
        await visitor.prepend([self])

    # -- YIELD ---------------------------------------------------------

    def _handle_yield(self, visitor: "InteractWalker", state: BridgeState) -> None:
        # Walker continues to the next IA in the weight chain. Bridge does not
        # re-enqueue itself. State is cleared to keep the visitor tidy.
        self._clear_state(visitor)

    # ------------------------------------------------------------------
    # Safety nets
    # ------------------------------------------------------------------

    async def _maybe_emit_safety_net(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
    ) -> None:
        """Fire the first-emit safety-net ack once per turn when the deadline lapses."""
        if state.last_emit_at is not None:
            return
        # Track whether the ack already fired (separate from last_emit_at so
        # subsequent EMITs don't re-trip the check).
        if state.helm_states.get("__bridge__", {}).get("safety_net_fired"):
            return
        elapsed_ms = (time.monotonic() - state.turn_started_at) * 1000.0
        if elapsed_ms < self.first_emit_timeout_ms:
            return
        if not self.safety_net_ack_text:
            return
        await self.publish(
            visitor=visitor,
            content=self.safety_net_ack_text,
            transient=True,
        )
        state.last_emit_at = time.monotonic()
        bucket = state.helm_states.setdefault("__bridge__", {})
        if isinstance(bucket, dict):
            bucket["safety_net_fired"] = True

    async def _safe_fallback(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
    ) -> None:
        """Publish ``denied_response_text`` and finalize the turn."""
        if self.denied_response_text:
            await self.publish(
                visitor=visitor,
                content=self.denied_response_text,
            )
        state.finalized = True
        self._clear_state(visitor)
