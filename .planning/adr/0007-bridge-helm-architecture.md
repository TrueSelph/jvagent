# ADR 0007 — Bridge + Helm architecture

**Status**: Proposed
**Date**: 2026-05-26
**Supersedes (in spirit)**: [`adr/0002-walker-revisit-cockpit.md`](0002-walker-revisit-cockpit.md) — the walker-revisit *pattern* stays; Bridge generalizes the *composition* on top of it.

## Context

Today, agentic behavior in jvagent is delivered by a single composition: `CockpitInteractAction` runs the full think/act/observe loop in one `InteractAction`. That works, but it forces every turn through a heavy reasoning model, even for trivial conversational replies, and gives operators no clean seam to route to a specialist `InteractAction` mid-turn without baking it into the cockpit.

We want to support sub-500ms first-response latency for trivial turns, peer-level delegation to rails `InteractAction`s, and clean persona-as-a-shift — without:

- modifying [`SPEC.md`](../SPEC.md) invariants 1–8,
- mutating `InteractWalker` / `response_bus` / `Conversation` / `Interaction` / `User` / `AccessControlAction`,
- forcing existing cockpit deployments to migrate,
- introducing a new walker-revisit mechanic ([`adr/0002`](0002-walker-revisit-cockpit.md) stands).

## Decision

Introduce the **Bridge + Helm** architecture as an additive composition on top of the existing harness. Bridge is a new `InteractAction`; Helms are new `Action` subclasses orchestrated by Bridge. The harness is unchanged.

```
BridgeInteractAction.execute(visitor)
  └─ step_machine
        ├─ resolve current_helm from BridgeState
        ├─ helm.step(visitor, bridge_state) → HelmStepResult
        ├─ process verb (EMIT | EXECUTE | SHIFT | DELEGATE | YIELD)
        └─ if revisit needed: visitor.prepend([self])
```

Each Bridge visit issues **at most one** model call (delegated to the current helm's `step()`). This preserves the one-model-call-per-walker-visit invariant established by ADR-0002.

### `HelmStepResult` verb set (v0.1)

Verbs are a closed enum revised additively. The original v0 set was
`EMIT | EXECUTE | SHIFT | DELEGATE | YIELD`. **v0.1** adds `CONTINUE` to support
helms that dispatch their own tools internally (e.g. `ReasoningHelm` running
the cockpit-style engine loop) and just need Bridge to re-enqueue them.
Additive verbs are non-breaking; breaking changes require ADR-0008+.

```python
# jvagent/action/helm/contracts.py (proposed)

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

HelmVerb = Literal["EMIT", "EXECUTE", "CONTINUE", "SHIFT", "DELEGATE", "YIELD"]

@dataclass(frozen=True)
class ToolCall:
    name: str                          # registry name, e.g. "memory_set", "action__handoff__open"
    arguments: Dict[str, Any]
    call_id: Optional[str] = None      # provider-issued id, when present

@dataclass(frozen=True)
class ShiftRecord:
    from_helm: Optional[str]           # None on initial entry
    to_helm: Optional[str]             # None on suppress
    reason: str
    ack_emitted: bool
    shift_index: int
    at_monotonic: float                # time.monotonic() — for per-shift duration math
    handoff_state: Optional[Dict[str, Any]] = None

@dataclass(frozen=True)
class EMIT:
    text: str
    finalize: bool = True              # if False, helm intends to revisit after the emit
    channel: Optional[str] = None      # passthrough to response_bus; None = default
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class EXECUTE:
    tool_calls: List[ToolCall]
    # Bridge owns the tool registry. After dispatch, Bridge persists state on
    # visitor._bridge_state and visitor.prepend([self]) to revisit the SAME helm.
    # Use EXECUTE when the helm wants Bridge to own tool dispatch (e.g. a fast
    # classifier helm with a small, allow-listed tool surface).

@dataclass(frozen=True)
class CONTINUE:
    reason: Optional[str] = None
    # Helm dispatched its own tools internally this visit and just needs another
    # walker visit. Bridge does NOT mutate helm_states / gear_trace / budget;
    # it only calls visitor.prepend([self]). Used by ReasoningHelm, which runs
    # the cockpit-style engine loop with its own tool registry.

@dataclass(frozen=True)
class SHIFT:
    target: str                        # helm name (no namespace) — resolved against Bridge's registered helms
    reason: str
    transient_ack: Optional[str] = None  # if set AND target.manifest.latency_class in {deliberate, long}, Bridge emits this before shifting
    handoff_state: Optional[Dict[str, Any]] = None
    interrupt: bool = False            # breaks turn-lock; only allowed for helms with manifest.can_interrupt = True

@dataclass(frozen=True)
class DELEGATE:
    interact_action: str               # canonical action name (matches info.yaml package.name)
    args: Optional[Dict[str, Any]] = None
    # Bridge resolves the IA via Action.get_action(<name>),
    # calls await action.execute(visitor) directly,
    # then visitor.prepend([self]) to finalize through Bridge.

@dataclass(frozen=True)
class YIELD:
    """Step aside; let the next IA in the agent's weight chain run.
    No revisit. Bridge exits cleanly. The agent's downstream IAs proceed."""
    pass

HelmStepResult = Union[EMIT, EXECUTE, CONTINUE, SHIFT, DELEGATE, YIELD]
```

**Semantics:**

| Verb | What Bridge does | Persists state? | Re-enqueues self? | Emits HELM_SHIFT event? |
|---|---|---|---|---|
| `EMIT(finalize=True)` | Publish via `response_bus`; finalize turn. | No (cleared) | No | No |
| `EMIT(finalize=False)` | Publish; revisit current helm. | Yes | Yes | No |
| `EXECUTE` | Dispatch tool calls (Bridge-owned registry); record results into helm-scoped state. | Yes | Yes (same helm) | No |
| `CONTINUE` | Re-enqueue current helm; **no** state mutation by Bridge. | No (Bridge does not touch state — helm owns it) | Yes | No |
| `SHIFT` | Emit `transient_ack` if eligible; check `tool:helm:{target}` AC; set `current_helm=target`; revisit. | Yes (with handoff_state on target's helm_states slot) | Yes | **Yes** |
| `DELEGATE` | Resolve named IA; `await ia.execute(visitor)`; revisit Bridge. | Yes | Yes | No (separate `DELEGATION` event for I) |
| `YIELD` | Exit Bridge; let walker continue weight chain. | Bridge clears its own state | No | No |

### `BridgeState`

```python
# jvagent/action/bridge/state.py (proposed)

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from .contracts import ShiftRecord

@dataclass
class BridgeState:
    current_helm: Optional[str] = None          # None at turn start; set on first resolution
    gear_trace: List[ShiftRecord] = field(default_factory=list)
    shift_count: int = 0
    turn_started_at: float = 0.0                # time.monotonic() at first Bridge visit this turn
    last_emit_at: Optional[float] = None        # for first-emit-timeout safety net
    helm_states: Dict[str, Any] = field(default_factory=dict)
                                                # per-helm scratch state, keyed by helm name
    delegated_action: Optional[str] = None      # set while a DELEGATE call is in flight
    shift_budget_remaining: int = 4             # decremented on each SHIFT; safe-fallback at 0
    finalized: bool = False                     # True once EMIT(finalize=True) has run
```

**Plumbing:** lives at `visitor._bridge_state`, parallel to `visitor._skill_state` (cockpit). Bridge initializes on first visit per turn. Cleared at `finalized=True` or `YIELD` to prevent leakage across turns.

**Invariant:** `current_helm` may be `None` only at the start of the first visit; after Bridge's initial helm resolution it MUST be a registered helm name until `finalized` or `YIELD`.

### Visitor attribute conventions

Two attributes Bridge sets on the walker. Both are part of the public Bridge–helm contract; helms read them via the named accessors below, not by reaching for the underscore name directly. Centralising the names lets a future revision switch to a different mechanism (e.g. a `BridgeContext` object passed into `step()`) in one place.

| Attribute | Constant | Set by | Read by | Lifetime |
|---|---|---|---|---|
| `visitor._bridge_state` | `BRIDGE_STATE_VISITOR_ATTR` in `bridge/state.py` | Bridge on first visit; cleared on `EMIT(finalize=True)` / `YIELD` | Bridge's verb dispatch and observability writers | Per-turn |
| `visitor._bridge_action` | `BRIDGE_VISITOR_ATTR` in `bridge/bridge_interact_action.py` | Bridge at the start of every `execute()` | Helms that need the IA in the walker queue — resolved via `BridgeInteractAction.from_visitor(visitor)` | Lifetime of the walker (Bridge does not clear it; harmless across turns since it always points at the same Bridge instance) |

Helms MUST NOT write to either attribute. Other patterns (cockpit, future helms) MAY define their own visitor attributes for their own state — by convention, prefix with an underscore and the pattern name (`_skill_state` for cockpit, `_bridge_state` / `_bridge_action` for Bridge, etc.).

### Manifest v0 schema

A pattern-agnostic `manifest:` block in `info.yaml`. Read by the loader into `Action.metadata['manifest']`. Helms and other patterns may consume it; the harness does not interpret it. **Lives in [`loader/info_yaml.py`](../../jvagent/action/loader/info_yaml.py), not under `bridge/` or `cockpit/`.**

```yaml
# info.yaml (any Action)
package:
  name: jvagent/feedback_interview
manifest:
  purpose: "Conduct a structured feedback interview with the user."
  activates_on:
    - "user agrees to give feedback"
    - "operator schedules an interview"
  terminates_when:
    - "all questions answered"
    - "user says STOP, cancel, or quit"
  latency_class: deliberate          # one of: instant | quick | deliberate | long
  turn_lock: true                    # if active, other helms must DELEGATE or interrupt
  interrupt_phrases:                 # phrases that may break turn_lock (Reflex only)
    - "stop"
    - "cancel"
    - "quit"
  expected_duration_seconds: 180.0
  can_interrupt: false               # may this helm issue SHIFT(interrupt=true)? default false
```

**Validation rules (loader-enforced):**

- `latency_class` MUST be one of `{instant, quick, deliberate, long}` if present; default `quick` if missing.
- `turn_lock` defaults to `false`.
- `can_interrupt` defaults to `false`. Set `true` only on Reflex-class helms or operator-defined overrides.
- `interrupt_phrases` is informational at v0 (consulted by Reflex prompt assembly at E); loader does not enforce.
- `expected_duration_seconds` is informational.
- A missing `manifest:` block yields `Manifest(latency_class="quick", turn_lock=False, can_interrupt=False)` defaults via `Action.get_manifest()`.
- Per-action override in `agent.yaml.context.manifest:` MUST validate against the same schema. Overrides shallow-merge (any present field replaces; absent fields fall back to info.yaml or defaults).

### AccessControl resource taxonomy

Bridge uses the existing `AccessControlAction` unchanged. New conventions, layered on top of the established `tool:{name}` / `skill:{name}`:

- `tool:helm:{name}` — gating a helm as a shift target. Checked at every `SHIFT` and at initial-helm resolution. `name` matches the helm's `class.name` (e.g., `tool:helm:ReflexHelm`).
- `tool:delegate:{action_name}` — gating a `DELEGATE` to a specific rails IA. `action_name` matches `info.yaml package.name`.

Default policy: if `AccessControlAction` is absent, both are permitted (existing fail-open default for missing AC). If AC is present, both follow standard (channel, user_id, resource) rules. Denied targets cause Bridge to safe-fallback (route to default reasoning helm OR emit a configured `denied_response_text`).

### Relation to ADR-0002

ADR-0002 mandates one model call per walker visit via `visitor.prepend([self])`. Bridge **upholds** this:

- Each `BridgeInteractAction.execute()` runs at most one helm `step()`.
- Each helm `step()` issues at most one LM call.
- Tool dispatch, shift bookkeeping, and AC checks happen between visits — same as cockpit.

ADR-0002 is **not superseded** as a mechanic; it is generalized — what cockpit does for one model loop, Bridge does for N composable model loops on the same revisit substrate.

### SPEC.md §3 addendum (proposed insertions — NOT applied)

Two additions to [`SPEC.md`](../SPEC.md):

1. **New §3.4 — Bridge multi-helm composition**:
   - States Bridge is a peer to Cockpit, both built atop the walker-revisit pattern in §3.3.
   - States the one-model-call-per-visit invariant applies inside helms too.
   - States manifest is pattern-agnostic and read at loader level.
2. **New §11 invariant #9** (additive — preserves 1–8): "`BridgeInteractAction` MUST issue at most one helm `step()` per `execute()` call, and each helm `step()` MUST issue at most one LM call. State persistence MUST live on `visitor._bridge_state`."

Neither addition modifies §3.1, §3.2, §3.3, §4, §5, §6, §7, §10, §11.1–11.8, §12. Both are scoped strictly under §3.4 / §11.9.

## Consequences

### Positive

- Latency: trivial turns can route through a fast helm (Reflex) that bypasses heavy reasoning.
- Composability: helms swap independently; new helms ship without harness changes.
- Observability: each shift is a discrete walker hop and a discrete `HELM_SHIFT` event.
- Reversibility: swapping `jvagent/bridge` for `jvagent/cockpit` in `agent.yaml` reverts cleanly. No data migration.
- Three patterns coexist (Rails, Cockpit, Bridge) without harness branching.

### Negative

- New surface area: Bridge + N helms = more code paths to test and monitor.
- Shift budget needed: bounded `BridgeState.shift_budget_remaining` (default 4) prevents helm ping-pong but adds a tuning knob.
- First-emit-timeout safety net required: if no helm emits within 800ms (default), Bridge fires a configured ack; tunable but adds latency floor.
- Manifest authoring discipline: helms make routing decisions from manifests, so stale/missing manifests degrade routing quality.

### Neutral

- `CockpitInteractAction` becomes a compat shim at C (internally constructs Bridge + ReasoningHelm). External cockpit YAML unchanged.
- `response_deliver_via_persona` tool becomes an alias issuing `SHIFT(target=PersonaHelm)` at G — original behavior preserved.

## Alternatives considered

1. **Multi-call inside one cockpit visit** — rejected. Breaks ADR-0002, hides intermediate state from walker hooks, defeats per-step access control.
2. **Spawn child walkers per helm** — rejected. State plumbing across walkers is brittle; jvspatial's walker semantics don't compose cleanly here.
3. **Cockpit as a helm registry (no Bridge)** — rejected. Conflates the reasoning loop with shift orchestration; forces cockpit-specific code paths into general helm dispatch.
4. **Hardcoded shift order (no manifest)** — rejected. Pattern-specific routing logic creeps into the harness or every helm; manifest centralizes per-action routing hints.

## Tuning

| Knob | Default | Notes |
|---|---|---|
| `Bridge.shift_budget_per_turn` | 4 | Hard cap on `SHIFT` verbs per turn (Open Q #4) |
| `Bridge.first_emit_timeout_ms` | 800 | If no `EMIT` by deadline, fires configured safety-net ack (Open Q #5) |
| `Bridge.safety_net_ack_text` | `"Working on it…"` | Emitted on first-emit timeout |
| `Bridge.default_helm` | `ReasoningHelm` | Initial helm if no manifest match |
| `Bridge.denied_response_text` | `"Sorry, I can't do that here."` | Emitted when all helms denied by AC |
| `ReflexHelm.latency_class` | `instant` | Set in helm's `info.yaml` |
| `ReflexHelm.can_emit_directly` | `true` | If `false`, Reflex is a pure classifier |

## References

- [`adr/0002-walker-revisit-cockpit.md`](0002-walker-revisit-cockpit.md) — load-bearing precedent
- [`.planning/BRIDGE-ROADMAP.md`](../BRIDGE-ROADMAP.md) — milestones A–K
- [`.planning/PATTERNS.md`](../PATTERNS.md) — pattern catalog (companion doc, drafted with this ADR)
- [`.planning/SPEC.md`](../SPEC.md) §3.3 — walker-revisit semantics
- [`docs/COCKPIT.md`](../../docs/COCKPIT.md) — current cockpit reference
