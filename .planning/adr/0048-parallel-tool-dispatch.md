# ADR 0048 — Parallel tool dispatch, opt-in

**Status**: Accepted
**Date**: 2026-09-05
**Relation**: Follow-up from [`.planning/reviews/2026-09-05-orchestrator-harness-audit.md`](../reviews/2026-09-05-orchestrator-harness-audit.md) (§"Parallel tool calls"). Builds on the native tool protocol ([ADR-0044](0044-native-tool-calling-protocol.md)), which already records grouped calls, and the tick extraction (audit follow-up S1), which made a tick's steps separable. Amends SPEC §3.3 invariant 1.

---

## 1. Context

Under the native protocol a provider may return several tool calls in one
response. The loop's invariant is *one model call per tick*, and until now it
also dispatched *one tool call per tick*: `parallel_tool_calls=False` was sent
to the provider and any extras it returned anyway were queued as pending
decisions and drained one per tick, each costing a tick of budget (though not a
model call).

That is the right default for a harness whose tools may have side effects and
whose guards (repeat, chain, grounding, companion gate) reason about one call at
a time. But for the common research shape — two or three independent lookups
before a synthesis — it forces sequential round-trips a mainstream model would
happily issue together, and the queue-drain path meant a provider that ignored
the flag got its calls answered one tick apart, in the same transcript group.

## 2. Decision

Give the Orchestrator's `max_concurrent_tools` its meaning (default `1`). The
key already existed as a reserved attribute (`0`, "0 = unbounded") that nothing
read; `0` is now read as `1`, so a store or YAML still carrying the reserved
value keeps one call per tick rather than waking up unbounded.

- **`1` (default)**: behaviour unchanged. `parallel_tool_calls=False` is sent
  where the provider understands it; extras are drained one per tick.
- **`> 1`**: the flag is not sent, and the loop prompt's "one call per step"
  rule becomes "independent calls together, dependent ones one at a time". In
  `_tick_tool`, after the lead call passes the guards, `_guarded_siblings`
  takes up to `max_concurrent_tools - 1` pending decisions from the lead's
  `group_id`, normalises each, and runs the **same pre-dispatch guards in
  order** on each. Siblings that pass are dispatched together by
  `_dispatch_batch` (`asyncio.gather` over `_dispatch_tool`); results are put
  back in decision order, each stamped with its own `call_id`, and then weighed
  by `_after_dispatch` one at a time. The first non-continue outcome ends the
  tick.

**Eligibility** is deliberately narrow: a call shares a tick only if its tool
exists, is not terminal, is not `reply`/`respond`/`use_skill`, and is not a meta
tool (`find_tool`, `load_tool`, …). No batching while a directive chain is
pending. Anything ineligible stays queued for its own tick exactly as before.

**Guards stay sequential.** A sibling that duplicates the lead (or an earlier
call in the window) is nudged, not run; a sibling the guards refuse without
appending a note gets a harness note carrying its call id, so the transcript
still answers every call the model made. The repeat guard's error flags are
recomputed per call after the batch (the single-dispatch path marks the most
recent entry, which is only correct for one call).

**Telemetry**: `parallel_batches` (ticks that dispatched more than one call) on
the `orchestrator_activation` event. Per-tool durations are recorded as before.

## 3. Consequences

- Operators who turn this on trade guard granularity for latency on independent
  lookups; side-effecting tools are still protected by the repeat guard, and
  egress/terminal/skill tools never batch, so a turn cannot end twice or
  reshape its surface mid-batch.
- SPEC §3.3 invariant 1 now reads *one model call per tick*, with the tool
  dispatch width a configuration choice. The activation budget still counts
  ticks, so a batch of three costs one tick.
- Providers that cannot express "single call" (`supports_parallel_tools is
  False` in the capability registry) are unaffected either way.
- The default stays `1`. Flipping it globally waits on live evidence that
  mainstream models group only genuinely independent calls; documenting the
  knob in the example agent belongs with the examples update.

## 4. Verification

`tests/action/orchestrator/test_parallel_dispatch.py`: default drains one per
tick; width 2 runs two siblings concurrently in one tick with results in
decision order and distinct call ids; egress and `use_skill` siblings are not
batched; a duplicate sibling is nudged and not run; an errored sibling marks
its own repeat-guard entry; `parallel_tool_calls=False` is omitted from the
provider request when the width is above 1; `parallel_batches` is reported.
`test_turn_boundary.py` gains the two new steps with their own size ceilings.
