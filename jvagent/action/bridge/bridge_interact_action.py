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
   - ``CONTINUE``             → re-enqueue current helm with no state mutation
     (helm dispatched its own tools internally).
   - ``SHIFT``                → AC-check ``tool:helm:{target}``; record
     ``ShiftRecord``; emit ack-on-shift when target is deliberate/long;
     decrement shift budget; re-enqueue.
   - ``DELEGATE``             → AC-check ``tool:delegate:{action}``; resolve
     and run the rails ``InteractAction`` inline; finalise (default) or
     re-enqueue (when ``follow_up=True``).
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
from jvagent.action.bridge.turn_lock import find_turn_lock_owner
from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import (
    CONTINUE,
    DELEGATE,
    EMIT,
    SHIFT,
    YIELD,
    HelmStepResult,
    ShiftRecord,
)
from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Latency classes that warrant an ack-on-shift when transient_ack is provided.
_ACK_ELIGIBLE_LATENCY_CLASSES = frozenset({"deliberate", "long"})

# Visitor attribute Bridge uses to stamp itself for helm lookup.
#
# Helms are ``Action`` subclasses, not ``InteractAction``s — they're not in
# the walker queue. When a helm needs the InteractAction reference that IS
# in the queue (e.g. ``ReasoningHelm`` calling ``curate_walk_path_for_cockpit``
# during routed-IA queue setup), it resolves the Bridge instance via
# :meth:`BridgeInteractAction.from_visitor` rather than reading the
# underscore attribute directly. Centralised here so the mechanism is
# changeable in one place.
BRIDGE_VISITOR_ATTR = "_bridge_action"


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
        default="…",
        description=(
            "Text published when the first-emit timeout fires. STATIC string "
            "— does NOT adapt to the user's language. Default is the "
            "universal ellipsis so multilingual deployments work without "
            "operator config. Override in agent.yaml for a single-language "
            'deployment (e.g. "Working on it…" for English-only). Set to '
            "an empty string to disable the safety-net publish."
        ),
    )
    denied_response_text: str = attribute(
        default="Sorry, I can't do that here.",
        description=(
            "Text published when the safe-fallback path activates (shift "
            "budget exhausted or AccessControl denied every reachable helm). "
            "STATIC string — does NOT adapt to the user's language. The "
            "default is English; override in agent.yaml for non-English "
            "deployments. This fires on rare error paths only — channel "
            "adapters that localise before publish are an alternative."
        ),
    )

    # ------------------------------------------------------------------
    # Visitor-side lookup (named contract for helms that need the Bridge IA)
    # ------------------------------------------------------------------

    @classmethod
    def from_visitor(cls, visitor: Any) -> Optional["BridgeInteractAction"]:
        """Return the Bridge instance orchestrating this visitor, or ``None``.

        Bridge stamps itself on the visitor at :meth:`execute` time via
        :data:`BRIDGE_VISITOR_ATTR`. Helms call this helper rather than
        reading the underscore attribute directly so the mechanism can
        evolve in one place. See ADR-0007 §"Visitor attribute conventions".

        Returns the stamped instance only when it is a ``BridgeInteractAction``;
        anything else (or a missing attribute) returns ``None``. Helms that
        require Bridge MUST handle ``None`` (typically by logging and
        proceeding without the queue-curation side-effect).
        """
        ia = getattr(visitor, BRIDGE_VISITOR_ATTR, None)
        return ia if isinstance(ia, cls) else None

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
        # Persist observability metadata BEFORE clearing the state object
        # so the interaction node carries a queryable trail of what the
        # turn actually did (BRIDGE-ROADMAP §I).
        try:
            self._persist_observability(visitor)
        except Exception as exc:
            logger.debug("bridge: observability persistence failed: %s", exc)
        if hasattr(visitor, BRIDGE_STATE_VISITOR_ATTR):
            try:
                delattr(visitor, BRIDGE_STATE_VISITOR_ATTR)
            except AttributeError:
                pass

    # ------------------------------------------------------------------
    # Observability (BRIDGE-ROADMAP §I)
    # ------------------------------------------------------------------

    def _record_helm_shift_event(
        self,
        visitor: "InteractWalker",
        record: ShiftRecord,
    ) -> None:
        """Append a ``helm_shift`` event to ``interaction.observability_metrics``.

        Mirrors the structure cockpit / jvagent core already uses (``event_type``
        + ``data`` + ``timestamp``) so the existing ``GET /logs/agents/{id}``
        query surface accepts these events without schema changes. See
        ``docs/logging.md`` for the canonical event taxonomy.
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        metrics = getattr(interaction, "observability_metrics", None)
        if metrics is None:
            return
        try:
            metrics.append(
                {
                    "event_type": "helm_shift",
                    "data": {
                        "from_helm": record.from_helm,
                        "to_helm": record.to_helm,
                        "reason": record.reason,
                        "ack_emitted": record.ack_emitted,
                        "shift_index": record.shift_index,
                        "at_monotonic": record.at_monotonic,
                    },
                    "timestamp": record.at_monotonic,
                }
            )
        except Exception as exc:
            logger.debug("bridge: failed to append helm_shift event: %s", exc)

    def _persist_observability(self, visitor: "InteractWalker") -> None:
        """Write Bridge's per-turn observability metadata onto the interaction.

        Three fields go onto ``Interaction.parameters`` (pattern-agnostic
        observability slot):

        - ``gear_trace`` — full list of :class:`ShiftRecord` dicts for the
          turn, including the initial helm resolution.
        - ``helm_timings_seconds`` — per-helm wall-clock totals.
        - ``helm_step_counts`` — per-helm step() call counts.

        Writes are best-effort: any exception is logged at DEBUG and
        observability silently degrades. We never block a turn on
        observability.
        """
        state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR, None)
        if state is None:
            return
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        params = getattr(interaction, "parameters", None)
        if params is None:
            return
        try:
            trace = [rec.to_dict() for rec in state.gear_trace]
        except Exception:
            trace = []
        payload = {
            "gear_trace": trace,
            "helm_timings_seconds": dict(state.helm_timings_seconds),
            "helm_step_counts": dict(state.helm_step_counts),
            "shift_count": state.shift_count,
            "turn_started_at": state.turn_started_at,
            "last_emit_at": state.last_emit_at,
        }
        try:
            if isinstance(params, dict):
                params["bridge_observability"] = payload
            elif isinstance(params, list):
                # Older interaction parameter shape — list of dicts.
                params.append(
                    {
                        "action_name": self.__class__.__name__,
                        "content": "bridge_observability",
                        "bridge_observability": payload,
                    }
                )
        except Exception as exc:
            logger.debug("bridge: failed to persist bridge_observability: %s", exc)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def execute(self, visitor: "InteractWalker") -> None:
        """Run one Bridge step: resolve helm, call ``step()``, dispatch verb."""
        # Stamp self on the visitor under ``BRIDGE_VISITOR_ATTR`` so helms
        # can reference the Bridge IA via :meth:`from_visitor` (e.g. to
        # pass to walker-queue curation that expects the IA actually
        # present in the queue — helms themselves are not). See ADR-0007
        # §"Visitor attribute conventions".
        try:
            setattr(visitor, BRIDGE_VISITOR_ATTR, self)
        except Exception:
            pass

        resolved = await self._resolve_helms_map()
        state = self._get_or_init_state(visitor)

        # Curate the walker queue on FIRST visit per turn so non-helm
        # InteractActions (intro, handoff, etc.) don't auto-run after
        # Bridge yields — that would emit a stray follow-up via the
        # walker's normal weight chain. Bridge owns the queue; other IAs
        # only run via explicit DELEGATE / DELEGATE-by-turn-lock.
        # Always-execute IAs are preserved so cross-cutting concerns
        # (logging, audit) still fire.
        if state.shift_count == 0:
            await self._curate_walker_queue(visitor)

        # Resolve current helm on first visit (or whenever it has been cleared).
        if state.current_helm is None:
            initial = self._pick_initial_helm(resolved)
            state.current_helm = initial.helm_name()
            rec = state.record_shift(
                from_helm=None,
                to_helm=state.current_helm,
                reason="bridge:initial",
                ack_emitted=False,
                at_monotonic=time.monotonic(),
                routing_source="initial",
            )
            self._record_helm_shift_event(visitor, rec)
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

        # Turn-lock detection (BRIDGE-ROADMAP §F). When a turn-locked
        # action is in flight (e.g. a multi-turn interview), ALWAYS
        # auto-DELEGATE to the lock owner rather than letting any helm
        # run a parallel model loop on the same turn. The lock owner's
        # manifest declares ``turn_lock: true``; its action class name
        # is captured by the detector.
        #
        # There is no helm-level "interrupt the lock" mechanism — that
        # was vestigial v0.1 surface and was removed in v0.2. Helms
        # don't know about active locks, so they can't reliably decide
        # whether a fragment continues the lock or breaks it. Lock-
        # breaking, when needed, lives in the IA's own intent
        # classifier (e.g. ``InterviewInteractAction`` reading
        # ``manifest.interrupt_phrases`` to detect CANCELLATION).
        lock_owner = await find_turn_lock_owner(visitor)
        if lock_owner is not None and lock_owner.action_name != helm.helm_name():
            logger.info(
                "bridge: turn-lock active on %r; auto-DELEGATE'ing "
                "instead of running helm %r.step()",
                lock_owner.action_name,
                helm.helm_name(),
            )
            await self._delegate_to_lock_owner(visitor, state, lock_owner)
            return

        # Per-helm wall-clock + step-count instrumentation (BRIDGE-ROADMAP §I).
        # Each step() call accrues to ``helm_timings_seconds[helm_name]`` so
        # operators can see where a turn's time was actually spent.
        helm_name = helm.helm_name()
        _t_step_start = time.monotonic()
        try:
            result = await helm.step(visitor, state)
        finally:
            elapsed = time.monotonic() - _t_step_start
            state.helm_timings_seconds[helm_name] = (
                state.helm_timings_seconds.get(helm_name, 0.0) + elapsed
            )
            state.helm_step_counts[helm_name] = (
                state.helm_step_counts.get(helm_name, 0) + 1
            )

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
        return isinstance(result, (EMIT, CONTINUE, SHIFT, DELEGATE, YIELD))

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
        # On a terminal EMIT, check whether any always_execute IA
        # (intro, handoff, etc.) has deposited an unexecuted directive
        # this turn. If so, route the helm's draft text through
        # PersonaAction so the directives compose with the helm's text
        # into one cohesive response. Without this hook the directive
        # sits unrendered (``executed=false``) and the user gets a bare
        # helm-voice response.
        handled_via_persona = False
        if verb.finalize:
            handled_via_persona = await self._publish_emit_via_persona_if_directives(
                visitor, state, verb
            )
        if not handled_via_persona:
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

    async def _publish_emit_via_persona_if_directives(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        verb: EMIT,
    ) -> bool:
        """Route a terminal helm EMIT through PersonaAction when pending
        IA directives are unrendered.

        Returns True iff persona handled the publish (caller skips its
        own ``self.publish``). False when no directives are pending, no
        PersonaAction is installed, or persona fails — caller falls
        back to direct publish.

        Always_execute IAs (intro, handoff) often deposit directives
        expecting a downstream persona render. In Bridge composition
        there is no walker-driven persona pass after Bridge yields,
        so without this hook those directives go unrendered.
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return False
        directives = getattr(interaction, "directives", None) or []
        if not directives:
            return False

        # Only fire when at least one directive is unexecuted. Persona
        # marks directives executed when it consumes them; without this
        # gate we'd re-render on subsequent turns that reuse the same
        # interaction view.
        pending = False
        for d in directives:
            if isinstance(d, dict) and not d.get("executed", False):
                pending = True
                break
        if not pending:
            return False

        # Guard against double-render within a single turn (e.g. helm
        # emits finalize=False then later finalize=True; we want one
        # persona pass, not two).
        bucket = state.helm_states.setdefault("__bridge__", {})
        if isinstance(bucket, dict) and bucket.get("directives_rendered"):
            return False

        persona: Any = None
        try:
            persona = await self.get_action("PersonaAction")
        except Exception:
            persona = None
        if persona is None:
            logger.debug(
                "bridge: pending directives but no PersonaAction installed; "
                "publishing helm EMIT text directly (directives unrendered)"
            )
            return False

        # Inject the helm's draft as a directive so persona composes it
        # alongside the IA directives. Mirrors PersonaHelm's pattern.
        text = (verb.text or "").strip()
        if text:
            try:
                await visitor.add_directive(f"Tell the user: {text}")
            except Exception as exc:
                logger.debug("bridge: failed to add helm draft directive: %s", exc)

        try:
            await persona.respond(interaction, visitor=visitor)
        except Exception as exc:
            logger.warning(
                "bridge: persona.respond raised during EMIT directive merge: %s",
                exc,
            )
            return False

        if isinstance(bucket, dict):
            bucket["directives_rendered"] = True
        return True

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

        rec = state.record_shift(
            from_helm=current_helm.helm_name(),
            to_helm=verb.target,
            reason=verb.reason,
            ack_emitted=ack_emitted,
            at_monotonic=time.monotonic(),
            handoff_state=verb.handoff_state,
            routing_source="helm_shift",
        )
        self._record_helm_shift_event(visitor, rec)
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

    # -- Walker-queue curation (BRIDGE-ROADMAP §F integration) ---------

    async def _curate_walker_queue(self, visitor: "InteractWalker") -> None:
        """Restrict the walker queue to ``{self} ∪ always_execute IAs``.

        Without this, IAs sitting in the agent's weight chain (intro,
        handoff, etc.) auto-run after Bridge yields — producing a stray
        persona-finalize LM call per turn and breaking the contract that
        Bridge owns the turn. Helms that want to invoke another IA must
        do so explicitly via the ``DELEGATE`` verb.
        """
        try:
            agent = await self.get_agent()
        except Exception as exc:
            logger.warning("bridge: get_agent failed during curate: %s", exc)
            return
        if agent is None:
            return
        try:
            from jvagent.action.interact.base import InteractAction

            actions_mgr = await agent.get_actions_manager()
            if actions_mgr is None:
                return
            all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
        except Exception as exc:
            logger.debug("bridge: curate actions enumeration failed: %s", exc)
            return

        always_run: List[Any] = []
        my_class = self.__class__.__name__
        user_id = getattr(visitor, "user_id", None)
        channel = getattr(visitor, "channel", "default") or "default"
        for action in all_enabled:
            if not isinstance(action, InteractAction):
                continue
            if action is self or action.__class__.__name__ == my_class:
                continue
            if not bool(getattr(action, "always_execute", False)):
                continue
            # AccessControl filter: ``always_execute`` IAs are implicit
            # delegations on every turn, so they're gated by the same
            # ``tool:delegate:{class_name}`` resource as explicit DELEGATE
            # targets. Denials drop the IA from the curated queue. Failing
            # closed here matches the explicit DELEGATE / SHIFT paths so
            # AC denials uniformly remove an action from the turn.
            try:
                await check_delegate_access(
                    agent,
                    action_name=action.__class__.__name__,
                    user_id=user_id,
                    channel=channel,
                )
            except BridgeAccessDenied as denied:
                logger.info(
                    "bridge: always_execute IA %r filtered by AC: %s",
                    action.__class__.__name__,
                    denied.resource,
                )
                continue
            always_run.append(action)

        # Sort by weight ascending so always-execute IAs visit in the
        # walker's normal order.
        always_run.sort(key=lambda a: int(getattr(a, "weight", 0)))
        combined: List[Any] = [self] + always_run

        try:
            await visitor.curate_walk_path(combined)
        except Exception as exc:
            logger.warning("bridge: curate_walk_path failed: %s", exc)

    # -- Turn-lock auto-delegate (BRIDGE-ROADMAP §F) -------------------

    async def _delegate_to_lock_owner(
        self,
        visitor: "InteractWalker",
        state: BridgeState,
        lock_owner: Any,
    ) -> None:
        """Run the turn-locked action directly, bypassing helm dispatch.

        Mirrors :meth:`_handle_delegate` except it skips the AccessControl
        check on ``tool:delegate:{name}`` — the lock-owner was already
        authorised when it acquired the lock — and uses the resolved
        action instance from the lock detector to skip a re-lookup.
        Persists ``delegated_action`` on the state so observability
        traces show the auto-delegate, then re-enqueues Bridge for the
        next walker visit.
        """
        action = lock_owner.action
        if action is None:
            logger.warning(
                "bridge: turn-lock owner %r has no resolved action; "
                "safe-falling back",
                lock_owner.action_name,
            )
            await self._safe_fallback(visitor, state)
            return
        # Record the auto-delegate in the gear trace so operators can
        # see when Bridge bypassed helm dispatch in favour of the lock
        # owner — labelled ``turn_lock`` so debugging the IA-selection
        # cascade is possible from the trace alone.
        rec = state.record_shift(
            from_helm=state.current_helm,
            to_helm=lock_owner.action_name,
            reason=f"bridge:turn_lock:{lock_owner.action_name}",
            ack_emitted=False,
            at_monotonic=time.monotonic(),
            routing_source="turn_lock",
        )
        self._record_helm_shift_event(visitor, rec)
        state.delegated_action = lock_owner.action_name
        try:
            await action.execute(visitor)
        except Exception:
            logger.exception(
                "bridge: turn-lock owner %r raised during execute",
                lock_owner.action_name,
            )
            state.delegated_action = None
            await self._safe_fallback(visitor, state)
            return
        state.delegated_action = None
        # Same finalize path as explicit DELEGATE — turn-locked IAs
        # (e.g. InterviewInteractAction) typically add a directive
        # expecting downstream persona rendering rather than publishing
        # directly. Without this call the directive sits unrendered and
        # the turn closes with response=None.
        await self._finalize_via_persona_if_directives(visitor)
        # Re-record the lock owner so subsequent turn-lock detection
        # finds it. Walker auto-records actions it visits via its queue;
        # this auto-delegate bypasses the queue. Skip when the IA
        # self-reports its lock has been released this turn (see the
        # mirror logic in ``_handle_delegate``).
        if await self._action_still_locking(action, visitor):
            interaction = getattr(visitor, "interaction", None)
            if interaction is not None:
                try:
                    interaction.record_action_execution(lock_owner.action_name)
                except Exception as exc:
                    logger.debug(
                        "bridge: failed to record turn-lock action %r: %s",
                        lock_owner.action_name,
                        exc,
                    )
        # Lock-owner has run; let the walker continue (don't re-enqueue
        # Bridge — the locked action drives its own flow until done).
        # If the locked action wants more Bridge turns, the next user
        # message will re-enter Bridge naturally.
        self._clear_state(visitor)

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

        # Record the delegation in the gear trace so operators can see
        # which IA the calling helm picked — labelled ``helm_delegate``
        # so the trace distinguishes a helm-initiated DELEGATE from
        # turn-lock auto-DELEGATE (``turn_lock``).
        rec = state.record_shift(
            from_helm=state.current_helm,
            to_helm=verb.interact_action,
            reason=f"bridge:delegate:{verb.interact_action}",
            ack_emitted=False,
            at_monotonic=time.monotonic(),
            routing_source="helm_delegate",
        )
        self._record_helm_shift_event(visitor, rec)
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
        # Record the delegated IA on the interaction so turn-lock
        # detection on the NEXT turn (``find_turn_lock_owner`` reads
        # ``interaction.actions``) can locate the active flow. Walker
        # auto-records actions it visits via its queue; DELEGATE bypasses
        # the queue (runs ``target.execute(visitor)`` inline), so we
        # have to record manually here.
        #
        # Skip recording when the IA self-reports its lock has been
        # released this turn (e.g. an interview just transitioned to
        # CANCELLED or COMPLETED). Otherwise the next user message would
        # trip turn-lock detection on a dead IA and Bridge would
        # auto-DELEGATE again — re-opening a fresh session.
        if await self._action_still_locking(target, visitor):
            interaction = getattr(visitor, "interaction", None)
            if interaction is not None:
                try:
                    interaction.record_action_execution(verb.interact_action)
                except Exception as exc:
                    logger.debug(
                        "bridge: failed to record DELEGATE action %r: %s",
                        verb.interact_action,
                        exc,
                    )
        # follow_up=True: the calling helm has more work to do (typically
        # more IAs in a sequenced chain). Re-enqueue Bridge so the helm
        # gets visited again; do NOT finalize via persona yet and do NOT
        # clear state — the helm will eventually emit the terminal verb
        # (DELEGATE follow_up=False, EMIT, or YIELD) that closes the turn.
        if verb.follow_up:
            await visitor.prepend([self])
            return
        # follow_up=False (default): DELEGATE hands the turn to the rails IA.
        # The IA may have:
        # (a) published a response directly via the response bus, OR
        # (b) added a directive to ``interaction.directives`` expecting a
        #     downstream PersonaAction to render it (the
        #     ``InterviewInteractAction`` / signup flow uses this path).
        # In Bridge composition there is no walker-driven persona pass
        # after Bridge yields — Bridge owns the turn. So if the IA left
        # directives behind without publishing, finalize via PersonaAction
        # here. Idempotent: if directives is empty or the IA already
        # published, this is a no-op.
        await self._finalize_via_persona_if_directives(visitor)
        # Do NOT re-enqueue Bridge — otherwise the current_helm (still
        # pointing at the helm that issued DELEGATE) would .step() again
        # and likely re-issue the same DELEGATE, producing an infinite
        # loop. Mirrors the behaviour of ``_delegate_to_lock_owner``.
        self._clear_state(visitor)

    async def _action_still_locking(
        self,
        action: Any,
        visitor: "InteractWalker",
    ) -> bool:
        """Return True if the rails IA still owns a turn lock after running.

        Bridge calls this after running an IA via DELEGATE (explicit or
        turn-lock auto-delegate). The IA gets the chance to declare its
        lock released — for example, an interview that just transitioned
        to ``CANCELLED`` or ``COMPLETED`` sets its session state and
        returns False here so Bridge skips recording the IA on
        ``interaction.actions``. Without this, the very next user
        message would re-trigger turn-lock detection, find the IA, and
        Bridge would auto-DELEGATE — re-opening a fresh session.

        IAs opt in by implementing
        ``async def is_actively_locking_turn(visitor) -> bool``. Default
        behaviour (no method on the IA) is True so existing rails IAs
        without lifecycle awareness keep the previous semantics
        (Bridge records them; turn-lock detection finds them next turn).
        """
        method = getattr(action, "is_actively_locking_turn", None)
        if method is None:
            return True
        try:
            result = method(visitor)
            if hasattr(result, "__await__"):
                result = await result
            return bool(result)
        except Exception as exc:
            logger.debug(
                "bridge: is_actively_locking_turn raised on %r: %s — "
                "assuming still locking",
                getattr(action, "__class__", type(action)).__name__,
                exc,
            )
            return True

    async def _finalize_via_persona_if_directives(
        self,
        visitor: "InteractWalker",
    ) -> None:
        """Render pending directives via PersonaAction.

        After DELEGATE runs a rails IA, that IA may have only added
        directives to ``interaction.directives`` (expecting PersonaAction
        to deliver). In Bridge there is no automatic persona pass —
        Bridge owns the turn end-to-end. This helper looks for unrendered
        directives and calls ``PersonaAction.respond()`` to publish a
        single user-facing reply.

        Safe no-op when:
        - PersonaAction is not installed on the agent
        - directives list is empty
        - ``interaction.response`` already has content (IA published
          directly)
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        existing_response = getattr(interaction, "response", None)
        if existing_response:
            return
        directives = getattr(interaction, "directives", None) or []
        if not directives:
            return
        persona: Any
        try:
            persona = await self.get_action("PersonaAction")
        except Exception:
            persona = None
        if persona is None:
            logger.debug(
                "bridge: DELEGATE finalize — no PersonaAction installed; "
                "%d directive(s) will go unrendered",
                len(directives),
            )
            return
        try:
            await persona.respond(interaction, visitor=visitor)
        except Exception:
            logger.exception("bridge: persona.respond failed during DELEGATE finalize")

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
