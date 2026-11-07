# Native request statistics — 2026-11-05

Parent `f40229400228f711a974ddfaa641b610acf0921e`. The preceding branch browser
receipts showed nonzero usage with zero attempt/duration statistics. Official
request-start/completion events were already persisted, but the accounting adapter
only generated synthetic attempts for unmatched billing. This impaired measurement.

## Thin adapter repair

Project official StepEvents into replay-stable per-run model_attempt metrics during
existing settlement (`jvagent/harness/accounting.py:216`, `:248`). Pair starts and
ends within the same run and step; rebuilding a run replaces only its own generated
attempts. Child and billing records retain their separate identities. This reads
the public StepStore protocol; no SDK internals, request wrapper, clock instrumentation,
retry scheduler or additional journal/store was added. Public boundary semantics:
[official Step Persistence documentation](https://pydantic.dev/docs/ai/harness/step-persistence/).

Metrics count SDK model-request boundaries, not transport retries. A fallback can
produce multiple billed responses within one boundary; billing receipts continue
to be counted independently. A repeated start without an independent covered step
is conservatively unreported rather than assumed paid. Historical snapshot-only
receipts retain their existing ordinal coverage; stronger correlation and operator
settlement remain future recovery work.

Interaction aggregation now includes completed native request durations in
total_duration_seconds, and all measured attempt durations in
model_attempt_duration_seconds. It counts unknown billing independently of outcome:
even a completed request with no receipt is unknown. Missing or invalid end times
retain duration=null and increment unmeasured_model_attempt_count rather than being
classified as measured zero (`jvagent/memory/interaction.py:604`, `:613`, `:718`).
Tokens/costs are not added again by timing events. Generated timings contain no
provider body, credentials or exception text. A counter recalculation is idempotent.

SDK boundary duration includes wrapper/persistence overhead; it is not pure HTTP
latency or decode time. Completed-boundary totals exclude failed/incomplete timings;
attempt totals include measured failures. These cumulative durations can overlap
for delegated runs and must not be used as wall-clock latency. Existing jvchat
token-per-second badges use this broader denominator, not provider decode throughput.

## Actual-model browser and SQL

Disposable PostgreSQL/app fixture on 8136/3136, real OpenAI GPT-4.1, unchanged
declarative membership skill, no patched execution functions. Runtime source blobs,
process identity, tool lifecycle, usage, tasks and responses are recorded in
[fresh SQL readback](2026-11-05-native-timing-final-smoke.json).
[Browser responses/events](2026-11-05-native-timing-browser.json) include visible
attempt/time badges; [reload and selection](2026-11-05-native-timing-restoration.json)
restore those statistics.

| Scenario | Outcome | SDK attempts | Measured SDK seconds | Interaction wall seconds |
|---|---|---:|---:|---:|
| Start/store/review | Delivered, task active | 4 | 6.014 | 8.035 |
| Confirm | Delivered, task completed, live session cleared | 2 | 2.555 | 3.648 |
| First cancellation probe | Completed before Stop; recorded as success | 1 | 11.642 | 11.937 |
| Second cancellation probe | Stop after persisted model start; cancelled | 1 | Unknown | Unset |

The three completed responses report 12,825 tokens and USD 0.026184 estimated.
One additional interrupted request has unknown usage/cost and one unmeasured
duration. Its partial zero-token receipt is not evidence of a free call. No provider
invoiced dollars returned. A successful first probe was not relabelled cancellation.
[Pre-Stop graph state](2026-11-05-native-timing-second-before-cancel-smoke.json)
shows the request was running with a model start before Stop; final SQL shows
run_failed, cancelled status, one unknown-cost attempt and no effects/tasks.
Generic cancelled Interaction timestamps and the empty cancelled-reply UI remain
limitations. No old loop/pilot execution modules loaded.

## Validation and remaining work

Two new regressions fail before the implementation: successful attempt/duration
projection and completed-without-receipt billing. Final native plus real-PG suites
pass 292 tests on each Python 3.11 and 3.12, including immediate/deferred PG readback.
Memory tests pass 199, with two skipped. Tests cover repeat settlement, missing and
reversed ends, cancellation, failure/known-response totals, shared budgets, children
and fallback/spend receipts. All-file hooks pass after test formatting is rechecked.
New-head CI and the normal full-suite pre-push gate are independent publication gates.

This repairs current statistics; it does not backfill all historical receipts,
prove provider-internal HTTP retry coverage, implement usage/effect reconciliation,
or qualify enterprise recovery. The compatibility, migration, legacy deletion,
provider/deployment and release gates remain open. No performance improvement is
claimed from this small cohort; workload thresholds and comparisons remain required.
