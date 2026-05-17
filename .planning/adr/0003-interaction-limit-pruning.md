# ADR 0003 — Rolling-window interaction pruning with per-call cap

**Status**: Accepted
**Date**: pre-2026

## Context

`Conversation` nodes accumulate `Interaction` children indefinitely. For long-running conversations:

- The context window passed to the model grows without bound.
- Storage grows linearly per turn.
- Walker traversal of the chain gets slower.

Two competing forces:

1. **Predictable latency**: every `add_interaction()` call must be bounded — a user message should not be punished because their conversation hit some retention threshold and now 10,000 interactions need eviction.
2. **Eventual completeness**: the configured `interaction_limit` should be respected; old interactions should not pile up indefinitely.

## Decision

Implement a **rolling-window pruning algorithm with a per-call work cap**:

- `Conversation.interaction_limit` (inherited from `Agent.interaction_limit`) sets the cap. `0` disables pruning entirely.
- `Conversation._prune_old_interactions()` ([`memory/conversation.py:297-367`](../../jvagent/memory/conversation.py)) removes the oldest `Interaction`s when `interaction_count > interaction_limit`.
- Per call, the algorithm removes at most `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (env, default `100`) interactions.
- The remainder is removed on subsequent `add_interaction()` calls, or via `Memory.apply_interaction_limit_pruning_for_connected_users()` for bulk re-prune.
- **The last `Interaction` is NEVER removed** — pruning halts if `next_interaction` is `None`.

## Consequences

### Positive
- **Bounded latency.** Worst-case per-call cost is bounded by the cap, not by the size of the overflow.
- **Tunable via env.** Ops can raise the cap on backends that handle deletes cheaply.
- **Disable-friendly.** Setting `interaction_limit = 0` disables pruning entirely; useful for autonomous agents that need full history.
- **Safe under churn.** Lowering `interaction_limit` doesn't trigger a stampede — it's gradual across subsequent appends.

### Negative
- **Eventual consistency** of the window size. If a user pauses, no further appends fire, and excess interactions linger until either they message again or someone calls `apply_interaction_limit_pruning_for_connected_users`.
- **No background sweeper** by default. Operators must understand that retention is append-driven.
- **Edge cases**: if the cap is set very low (e.g., `1`) but `interaction_limit` is also low and many users are over, recovery takes time.

## Invariants

1. Never delete the last `Interaction`.
2. After pruning, `last_interaction_id` is verified and rebuilt by traversal if stale.
3. `interaction_count` decrements once per successful removal.
4. Edge rewiring: `Conv → current` is disconnected and `Conv → next` is established before deleting `current`. Order matters for query correctness mid-pass.

## Alternatives considered

1. **Unbounded prune in one call** — rejected: latency spike for users on overflowing conversations.
2. **Background sweeper** — rejected: out-of-band reliability concerns + extra scheduler dependency. May revisit when jvagent integrates a task queue.
3. **Time-based retention** instead of count-based — rejected: count is the right knob for context-window cost; time can be a future overlay.
4. **Soft-delete (mark as pruned, keep row)** — rejected: complicates queries and walker traversal; the storage savings of hard delete dominate.

## Tuning

| Knob | Default | Effect |
|---|---|---|
| `Agent.interaction_limit` | `0` (disabled) | Per-agent default window |
| `Conversation.interaction_limit` | inherits from Agent | Per-conversation override |
| `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` env | `100` | Per-call work cap |

## References

- [`SPEC.md`](../SPEC.md) §5.3
- [`memory-and-pruning.md`](../memory-and-pruning.md) §5 — algorithm walkthrough
- Regression tests: `tests/test_comprehensive_pruning.py`, `tests/test_pruning_fix.py`
