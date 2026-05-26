"""``BridgeState`` — per-turn state object plumbed on ``visitor._bridge_state``.

Bridge initializes a fresh ``BridgeState`` on the first walker visit of a
turn and clears it once the turn finalizes (via ``EMIT(finalize=True)`` or
``YIELD``). The dataclass is intentionally simple (no methods that mutate
helm-owned fields) so Bridge ownership boundaries stay clear.

Parallel to ``visitor._skill_state`` (cockpit). The two fields do not interact;
a single agent never installs both Bridge and Cockpit (see PATTERNS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from jvagent.action.helm.contracts import ShiftRecord

BRIDGE_STATE_VISITOR_ATTR = "_bridge_state"
"""The attribute name Bridge sets on the walker. Centralised so tests can
patch it without string-literal drift."""

DEFAULT_SHIFT_BUDGET = 4
DEFAULT_FIRST_EMIT_TIMEOUT_MS = 800


@dataclass
class BridgeState:
    """Per-turn Bridge state stored on the walker.

    Field ownership:

    - **Bridge owns**: ``current_helm``, ``gear_trace``, ``shift_count``,
      ``turn_started_at``, ``last_emit_at``, ``delegated_action``,
      ``shift_budget_remaining``, ``finalized``.
    - **Helms own** (read/write): ``helm_states[<helm_name>]``.

    Helms MUST NOT mutate the Bridge-owned fields. Bridge MUST NOT mutate
    arbitrary entries under ``helm_states`` — only set/clear the slot for the
    target helm during ``SHIFT`` (when ``handoff_state`` is provided).
    """

    current_helm: Optional[str] = None
    gear_trace: List[ShiftRecord] = field(default_factory=list)
    shift_count: int = 0
    turn_started_at: float = 0.0
    last_emit_at: Optional[float] = None
    helm_states: Dict[str, Any] = field(default_factory=dict)
    delegated_action: Optional[str] = None
    shift_budget_remaining: int = DEFAULT_SHIFT_BUDGET
    finalized: bool = False
    # Per-helm wall-clock time accumulated across step() calls this turn.
    # Keyed by helm_name; written by Bridge's step machine (BRIDGE-ROADMAP §I).
    helm_timings_seconds: Dict[str, float] = field(default_factory=dict)
    # Per-helm step counts. Useful for spotting helms that loop many
    # times via CONTINUE before producing an EMIT.
    helm_step_counts: Dict[str, int] = field(default_factory=dict)

    def record_shift(
        self,
        *,
        from_helm: Optional[str],
        to_helm: Optional[str],
        reason: str,
        ack_emitted: bool,
        at_monotonic: float,
        handoff_state: Optional[Dict[str, Any]] = None,
        routing_source: Optional[str] = None,
    ) -> ShiftRecord:
        """Append a ``ShiftRecord`` and increment ``shift_count``.

        Returns the appended record so callers can use its ``shift_index``.
        ``routing_source`` should be one of the labels enumerated on
        :class:`ShiftRecord` so debugging the IA-selection cascade is
        possible from observability data alone.
        """
        rec = ShiftRecord(
            from_helm=from_helm,
            to_helm=to_helm,
            reason=reason,
            ack_emitted=ack_emitted,
            shift_index=self.shift_count,
            at_monotonic=at_monotonic,
            handoff_state=handoff_state,
            routing_source=routing_source,
        )
        self.gear_trace.append(rec)
        self.shift_count += 1
        return rec
