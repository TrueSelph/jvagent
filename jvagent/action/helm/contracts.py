"""HelmStepResult verb set and supporting dataclasses.

The verb set at **v0.1** is the closed enum below. Verbs are additive across
minor revisions per ADR-0007: ``CONTINUE`` joined v0 as the first additive
verb (v0.1) to support helms that internally dispatch their own tools and
just need Bridge to re-enqueue them. Breaking changes require ADR-0008+.

Verb semantics (Bridge dispatch contract):

==================  ==================================================================
Verb                Bridge behavior
==================  ==================================================================
``EMIT``            Publish ``text`` via the agent's response bus.
                    If ``finalize=True`` (default), turn ends and Bridge clears state.
                    If ``finalize=False``, Bridge re-enqueues the current helm.
``EXECUTE``         Dispatch ``tool_calls`` (Bridge-side tool registry; used by helms
                    that delegate tool execution to Bridge). Persists results into
                    helm-scoped state and re-enqueues the helm.
``CONTINUE``        Re-enqueue the current helm with no Bridge-side state mutation.
                    Used by helms that dispatch their own tools internally (e.g.
                    ``ReasoningHelm`` running the cockpit-style engine loop) and
                    simply need another walker visit to continue.
``SHIFT``           Switch to ``target`` helm. Emits ``transient_ack`` first when the
                    target's manifest declares a ``deliberate`` or ``long`` latency
                    class. AccessControl gated by ``tool:helm:{target}``.
``DELEGATE``        Resolve a rails ``InteractAction`` by name and run it inline; then
                    re-enqueue Bridge. AccessControl gated by
                    ``tool:delegate:{action_name}``.
``YIELD``           Exit Bridge cleanly so the walker continues the weight chain.
==================  ==================================================================

State persistence and ``visitor.prepend([self])`` semantics are documented in
:mod:`jvagent.action.bridge.state` and :mod:`jvagent.action.bridge.bridge_interact_action`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

HelmVerb = Literal["EMIT", "EXECUTE", "CONTINUE", "SHIFT", "DELEGATE", "YIELD"]


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation requested by a helm via ``EXECUTE``.

    ``name`` matches the registered tool name in Bridge's tool registry (e.g.
    ``"memory_set"``, ``"action__handoff__open"``). ``call_id`` is the
    provider-issued id when present; helms MAY omit it.
    """

    name: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None


@dataclass(frozen=True)
class ShiftRecord:
    """A single entry in ``BridgeState.gear_trace``.

    Emitted once per ``SHIFT`` verb (including the initial helm resolution at
    turn start, where ``from_helm=None``). The ``HELM_SHIFT`` observability
    event for milestone I derives directly from these records.
    """

    from_helm: Optional[str]
    to_helm: Optional[str]
    reason: str
    ack_emitted: bool
    shift_index: int
    at_monotonic: float
    handoff_state: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this record for persistence on ``Interaction.parameters``.

        ``handoff_state`` is dropped if it carries unserialisable values —
        gear_trace is observability metadata, not authoritative state, so
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
        }


@dataclass(frozen=True)
class EMIT:
    """Publish a user-facing response.

    When ``finalize=True`` (the default), the turn ends and Bridge clears
    ``BridgeState``. When ``finalize=False``, Bridge publishes the partial
    output and re-enqueues the current helm so it can continue.
    """

    text: str
    finalize: bool = True
    channel: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EXECUTE:
    """Dispatch a batch of tool calls via Bridge's tool registry.

    Bridge runs each call against its registry (Bridge tool dispatch is wired
    in later milestones — at B the verb is accepted but no real registry is
    invoked). After dispatch, Bridge persists state and revisits the **same**
    helm so it can observe results.

    Use this when the helm wants Bridge to own dispatch (so the registry is
    a Bridge-level service, queryable from any helm). Use :class:`CONTINUE`
    when the helm dispatches its own tools internally.
    """

    tool_calls: List[ToolCall]


@dataclass(frozen=True)
class CONTINUE:
    """Re-enqueue the current helm with no Bridge-side state mutation.

    The helm has already done its work for this visit — typically dispatched
    its own tool calls and recorded results in its private slot of
    ``BridgeState.helm_states`` — and just needs another walker visit to
    continue the loop. Bridge calls ``visitor.prepend([self])`` and returns.

    Compared to ``EXECUTE``:

    - ``EXECUTE`` asks Bridge to run a tool batch on the helm's behalf.
    - ``CONTINUE`` only asks Bridge to schedule the next visit.

    ``ReasoningHelm`` uses ``CONTINUE`` because the cockpit-style engine
    loop manages its own tool dispatch.
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

    ``interrupt=True`` is reserved for helms whose manifest declares
    ``can_interrupt: true`` (Reflex by default). It breaks any active turn-lock
    in milestone F; at B the field is accepted but turn-lock is not yet
    implemented.
    """

    target: str
    reason: str
    transient_ack: Optional[str] = None
    handoff_state: Optional[Dict[str, Any]] = None
    interrupt: bool = False


@dataclass(frozen=True)
class DELEGATE:
    """Yield to a rails ``InteractAction`` for a single execution.

    Bridge resolves the action via ``Action.get_action(<interact_action>)``,
    calls ``await action.execute(visitor)`` inline, then revisits Bridge so the
    helm can continue. AccessControl: ``tool:delegate:{interact_action}``.
    """

    interact_action: str
    args: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class YIELD:
    """Exit Bridge cleanly; let the walker continue the weight chain.

    No revisit, no further helm work this turn. Bridge clears its own state.
    """


HelmStepResult = Union[EMIT, EXECUTE, CONTINUE, SHIFT, DELEGATE, YIELD]
"""Discriminated union of all verbs a helm's ``step()`` may return.

At v0.1 this is a closed set; additive verbs are non-breaking per ADR-0007."""
