"""HelmStepResult verb set and supporting dataclasses.

The verb set at **v0.2** is the closed enum below. Verbs are additive across
minor revisions per ADR-0007. Recent changes:

- **v0.2**: ``EXECUTE`` removed (never used by any shipped helm; reserved
  surface confused the contract). ``SHIFT.interrupt`` removed (Bridge's
  current policy is to ALWAYS auto-DELEGATE on active turn-locks; lock-
  breaking lives in the rails IA's intent classifier).
- **v0.1**: ``CONTINUE`` added to support helms that internally dispatch
  their own tools and just need Bridge to re-enqueue them; ``DELEGATE``
  gained ``follow_up: bool`` to support multi-IA dispatch chains.

Breaking changes require ADR-0008+.

Verb semantics (Bridge dispatch contract):

==================  ==================================================================
Verb                Bridge behavior
==================  ==================================================================
``EMIT``            Publish ``text`` via the agent's response bus.
                    If ``finalize=True`` (default), turn ends and Bridge clears state.
                    If ``finalize=False``, Bridge re-enqueues the current helm.
``CONTINUE``        Re-enqueue the current helm with no Bridge-side state mutation.
                    Used by helms that dispatch their own tools internally (e.g.
                    ``ReasoningHelm`` running the engine-style engine loop) and
                    simply need another walker visit to continue.
``SHIFT``           Switch to ``target`` helm. Emits ``transient_ack`` first when the
                    target's manifest declares a ``deliberate`` or ``long`` latency
                    class. AccessControl gated by ``tool:helm:{target}``.
``DELEGATE``        Resolve a rails ``InteractAction`` by name and run it inline.
                    If ``follow_up=False`` (default), Bridge persona-finalises and
                    closes the turn. If ``follow_up=True``, Bridge re-enqueues the
                    calling helm so it can dispatch more IAs in a chain.
                    AccessControl gated by ``tool:delegate:{action_name}``.
``YIELD``           Exit Bridge cleanly so the walker continues the weight chain.
==================  ==================================================================

State persistence and ``visitor.prepend([self])`` semantics are documented in
:mod:`jvagent.action.bridge.state` and :mod:`jvagent.action.bridge.bridge_interact_action`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Union

HelmVerb = Literal["EMIT", "CONTINUE", "SHIFT", "DELEGATE", "YIELD"]


@dataclass(frozen=True)
class ShiftRecord:
    """A single entry in ``BridgeState.shift_log``.

    Emitted once per ``SHIFT`` verb (including the initial helm resolution at
    turn start, where ``from_helm=None``). The ``helm_shift`` observability
    event on ``Interaction.observability_metrics`` derives directly from
    these records.

    ``routing_source`` labels which layer of the IA-selection cascade
    decided this transition — useful for debugging "why did this helm
    or IA get picked?" without checking three code paths. Layer labels:

    - ``"initial"`` — Bridge's initial helm resolution at turn start.
    - ``"turn_lock"`` — auto-DELEGATE to a turn-locked IA.
    - ``"helm_shift"`` — a helm emitted ``SHIFT`` to another helm.
    - ``"helm_delegate"`` — a helm emitted ``DELEGATE`` to a rails IA.
    - ``"safe_fallback"`` — Bridge's safe-fallback path (AC denial,
      shift budget exhausted, unresolvable target).
    """

    from_helm: Optional[str]
    to_helm: Optional[str]
    reason: str
    ack_emitted: bool
    shift_index: int
    at_monotonic: float
    handoff_state: Optional[Dict[str, Any]] = None
    routing_source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this record for persistence on ``Interaction.parameters``.

        ``handoff_state`` is dropped if it carries unserialisable values —
        shift_log is observability metadata, not authoritative state, so
        it should never fail to persist because of one record.
        """
        try:
            handoff: Optional[Dict[str, Any]] = (
                dict(self.handoff_state) if self.handoff_state is not None else None
            )
        except Exception:
            handoff = None
        return {
            "from_helm": self.from_helm,
            "to_helm": self.to_helm,
            "reason": self.reason,
            "ack_emitted": self.ack_emitted,
            "shift_index": self.shift_index,
            "at_monotonic": self.at_monotonic,
            "handoff_state": handoff,
            "routing_source": self.routing_source,
        }


@dataclass(frozen=True)
class EMIT:
    """Publish a user-facing response.

    When ``finalize=True`` (the default), the turn ends and Bridge clears
    ``BridgeState``. When ``finalize=False``, Bridge publishes the partial
    output and re-enqueues the current helm so it can continue.

    Persona-stylisation fields (consumed by Bridge's ``_handle_emit``):

    - ``via_persona``: when True, Bridge routes ``text`` through
      ``PersonaAction.respond`` for tone / style polish before publishing.
      ReasoningHelm sets this on its final engine output so the agent's
      persona wraps the engine's raw text. Reflex's trivial EMITs leave
      it False (no need to LLM-rewrite a one-word greeting).
    - ``response_mode``: forwarded to the persona-delivery helper.
      Supported values: ``"publish"`` (raw publish through persona),
      ``"respond"`` (persona ``respond()`` call), ``"verbatim_final"``
      (skip persona, publish raw — used for skill-driven final outputs
      where the skill already produced final-form text).
    - ``degenerate_max_chars``: skip persona stylisation when ``text``
      is at most this many characters (short outputs read worse after
      persona rewording). 0 disables the heuristic.

    These fields are read by Bridge — helms set them but don't act on
    them directly. They generalise the per-helm
    ``deliver_final_response`` path that ReasoningHelm used to call
    in-line. Phase-2 distillation pushed that surface up into Bridge.
    """

    text: str
    finalize: bool = True
    channel: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    via_persona: bool = False
    response_mode: str = "publish"
    degenerate_max_chars: int = 0


@dataclass(frozen=True)
class CONTINUE:
    """Re-enqueue the current helm with no Bridge-side state mutation.

    The helm has already done its work for this visit — typically dispatched
    its own tool calls and recorded results in its private slot of
    ``BridgeState.helm_states`` — and just needs another walker visit to
    continue the loop. Bridge calls ``visitor.prepend([self])`` and returns.

    ``ReasoningHelm`` uses ``CONTINUE`` because the engine-style engine
    loop manages its own tool dispatch internally.
    """

    reason: Optional[str] = None


@dataclass(frozen=True)
class SHIFT:
    """Switch the current helm.

    ``target`` MUST resolve to a helm registered on the Bridge instance. The
    AccessControl check uses ``tool:helm:{target}``; denials route to Bridge's
    safe-fallback path.

    ``transient_ack`` is published only when the target's manifest declares
    ``latency_class in {"deliberate", "long"}`` (see Bridge implementation).

    ``handoff_state`` is stored on ``BridgeState.helm_states[target]`` for the
    target helm to consume on its next ``step`` call.

    Note on turn-locks: Bridge's policy is to ALWAYS auto-DELEGATE to an
    active lock owner before consulting any helm. There is no helm-level
    "interrupt the lock" mechanism — lock-breaking, when needed, lives in
    the rails IA's own intent classifier (e.g. interview's CANCELLATION
    intent reading ``manifest.interrupt_phrases``).
    """

    target: str
    reason: str
    transient_ack: Optional[str] = None
    handoff_state: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class DELEGATE:
    """Yield to a rails ``InteractAction`` for a single execution.

    Bridge resolves the action via ``Action.get_action(<interact_action>)``,
    calls ``await action.execute(visitor)`` inline, then either finalises
    the turn (default) or re-enqueues itself so the calling helm can
    continue dispatching. AccessControl: ``tool:delegate:{interact_action}``.

    ``follow_up`` controls Bridge's behaviour after the IA runs:

    - ``False`` (default) — Bridge finalises via persona if directives are
      pending, then clears Bridge state and exits the turn. Used when the
      DELEGATE is the LAST step the helm wants to take (single-IA dispatch
      or the final IA in a chain).
    - ``True`` — Bridge re-enqueues itself via ``visitor.prepend([self])``
      WITHOUT clearing state and WITHOUT calling persona-finalize. The
      helm gets visited again and is responsible for either issuing the
      next DELEGATE in the chain (``follow_up=True``), the final DELEGATE
      (``follow_up=False``), or another terminal verb. Used to sequence
      multiple rails IAs through a single helm-driven chain — e.g. when
      the router returned multiple ``routing.interact_actions`` and each
      needs to run in weight order before persona-finalize.

    Helms MUST set ``follow_up=False`` on the last DELEGATE of a chain
    so persona-finalize runs and the turn closes. A helm that keeps
    returning ``follow_up=True`` forever is bounded only by jvspatial's
    ``max_visits_per_node=100``; pair with the helm's own iteration cap.
    """

    interact_action: str
    args: Optional[Dict[str, Any]] = None
    follow_up: bool = False


@dataclass(frozen=True)
class YIELD:
    """Exit Bridge cleanly; let the walker continue the weight chain.

    No revisit, no further helm work this turn. Bridge clears its own state.
    """


HelmStepResult = Union[EMIT, CONTINUE, SHIFT, DELEGATE, YIELD]
"""Discriminated union of all verbs a helm's ``step()`` may return.

At v0.2 this is a closed set; additive verbs are non-breaking per ADR-0007."""
