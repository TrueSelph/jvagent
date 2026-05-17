# ADR 0002 — Walker-revisit pattern for the Cockpit

**Status**: Accepted
**Date**: 2025 (cockpit introduction)

## Context

The cockpit grants the language model full agency over harness services and action tools. The model loop is *think → act → observe → repeat* until it produces text without tool calls or a termination condition fires.

Two implementations are possible:

1. **Internal loop**: inside `CockpitInteractAction.execute(visitor)`, run a `while not done:` that calls the model, dispatches tools, and re-loops — all in one walker visit.
2. **Walker revisit**: `execute(visitor)` does **one** model call per visit. When the model returns tool calls, the action persists state on `visitor._skill_state` and re-adds itself to the walk path via `visitor.prepend([self])`. The walker visits the cockpit again on the next iteration.

## Decision

Use **walker revisit**. Each call to `execute()` performs exactly one model call; if the model returns tool calls, the action re-enqueues itself and persists state.

Source: [`jvagent/action/cockpit/cockpit_interact_action.py:79`](../../jvagent/action/cockpit/cockpit_interact_action.py).

## Consequences

### Positive
- **The walker sees every model call.** Per-step concerns — streaming flush, access control checks, action recording, response-bus commits — happen naturally between visits.
- **Stuck detection and `max_iterations` enforcement** integrate with the walker's existing visit counter and step bound (`max_visits_per_node=100`, jvspatial-side).
- **State is explicit** on `visitor._skill_state`. Debugging an iteration is reading a single object, not unwinding a Python stack frame.
- **Composability**: other `InteractAction`s can run between cockpit visits. The walker's queue order — not a hidden inner loop — drives interleaving.
- **Graceful termination**. The walker's outer `max_execution_time` enforces global wall-clock without the cockpit having to reimplement it.

### Negative
- **Walker traversal overhead per iteration.** Each visit re-resolves the action node, runs visit hooks, and consults the queue. Measured cost is small relative to model latency.
- **Slightly more complex code path** — state-restore on each visit, prepend semantics — than a flat `while` loop.
- **`max_visits_per_node` defaults to 100** in jvspatial. The cockpit's `max_iterations` (default 25) sits well under this, but be careful not to bypass.

## Alternatives considered

1. **Internal loop** (rejected — see above; loses per-iteration walker visibility, harder access control).
2. **Hybrid (loop with periodic walker yield)** — rejected: same complexity as revisit, less clean.
3. **Multi-walker (spawn child walkers per iteration)** — rejected: state plumbing nightmare.

## Tuning

| Knob | Default | Source |
|---|---|---|
| `max_iterations` | 25 | [`cockpit_interact_action.py:105`](../../jvagent/action/cockpit/cockpit_interact_action.py) |
| `max_duration_seconds` | 300.0 | [`cockpit_interact_action.py:106`](../../jvagent/action/cockpit/cockpit_interact_action.py) |
| `stuck_detection_window` | 4 | [`cockpit_interact_action.py:187`](../../jvagent/action/cockpit/cockpit_interact_action.py) |
| `stuck_intent_jaccard_threshold` | 0.65 | [`cockpit_interact_action.py:188`](../../jvagent/action/cockpit/cockpit_interact_action.py) |

## References

- [`SPEC.md`](../SPEC.md) §3.3
- [`docs/COCKPIT.md`](../../docs/COCKPIT.md)
- [`jvagent/action/cockpit/CLAUDE.md`](../../jvagent/action/cockpit/CLAUDE.md)
